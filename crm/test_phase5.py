from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import Group, Permission
from django.db import close_old_connections, connection
from django.test import Client

from .corrections import (
    approve_cost_correction,
    cost_correction_rows,
    preview_cost_correction,
    reject_cost_correction,
    submit_cost_correction,
)
from . import corrections as correction_service
from .fulfillment import (
    confirm_cost_correction_commission_decision,
    deliver_on_site,
    review_commission,
    update_outbound_freight,
)
from .inventory_adjustments import (
    approve_stock_count,
    create_inventory_gift,
    create_inventory_split,
    create_stock_count,
    revise_stock_count,
    resolve_damaged_record,
    stock_count_rows,
)
from .inventory_replay import InventoryReplay, run_and_validate
from .models import (
    BusinessControl,
    BusinessOperation,
    CommissionAdjustment,
    CommissionReview,
    CostCorrection,
    DamageResolution,
    DamagedRecord,
    GiftLine,
    GiftRecord,
    InventoryBalance,
    InventoryHold,
    InventoryMovement,
    PaymentRecord,
    Receipt,
    ReceiptLine,
    SalesOrder,
    SalesOrderLine,
    SplitRecord,
    StockCount,
    StockCountLine,
)
from .operations import OperationError, confirm_receipt, preview_receipt, release_hold
from .opening import confirm_opening, preview_opening
from .permissions import ROLE_OWNER, ROLE_WAREHOUSE
from .test_phase2 import opening_workbook, product_row, stock_row
from .test_phase4 import Phase4Base


class Phase5Base(Phase4Base):
    def setUp(self):
        super().setUp()
        # Phase3Base creates a fixture balance directly. Stage 5 replay tests
        # must start from balances backed by real opening/receipt operations.
        self.balance.delete()
        self.balance = None

    def receive(self, sku=None, *, warehouse=None, quantity_value, price, reserved=0, damaged=0):
        payload = self.receipt_payload(
            sku or self.sku,
            quantity_value=quantity_value,
            price=price,
            warehouse=warehouse,
            reserved=reserved,
            damaged=damaged,
        )
        _, result = self.preview_and_receive(payload)
        return Receipt.objects.get(pk=result["receipt_id"])

    def receive_lines(self, warehouse, lines):
        payload = {
            "warehouse_id": warehouse.pk,
            "supplier_id": self.supplier.pk,
            "lines": [{
                "sku_id": sku.pk,
                "quantity": quantity_value,
                "purchase_price": str(price),
                "inbound_freight": "0.000",
                "reserved_quantity": 0,
                "damaged_quantity": 0,
                "low_stock_threshold": None,
            } for sku, quantity_value, price in lines],
        }
        _, result = self.preview_and_receive(payload)
        return Receipt.objects.get(pk=result["receipt_id"])

    def balance_for(self, sku=None, warehouse=None):
        return InventoryBalance.objects.get(sku=sku or self.sku, warehouse=warehouse or self.warehouse)

    def correction_data(self, receipt, *, line_number=1, reason="Correct purchase source", warehouse=None, **patch_fields):
        data = {
            "receipt_id": receipt.pk,
            "reason": reason,
            "lines": [{"line_number": line_number, **patch_fields}],
        }
        if warehouse is not None:
            data["warehouse_id"] = warehouse.pk
        return data

    def approve_correction(self, correction):
        correction.refresh_from_db()
        return approve_cost_correction(self.owner, correction.pk, {
            "operation_key": str(uuid4()),
            "expected_version": correction.version,
        })

    def approve_count(self, count, actor=None, key=None):
        actor = actor or self.owner
        count.refresh_from_db()
        versions = {
            str(line.pk): line.balance.version
            for line in count.lines.select_related("balance").order_by("id")
        }
        return approve_stock_count(actor, count.pk, {
            "operation_key": str(key or uuid4()),
            "expected_version": count.version,
            "balance_versions": versions,
        })

    def assert_replay(self):
        try:
            return run_and_validate()
        except OperationError as exc:
            replayed = InventoryReplay().run().order_lines
            self.fail(f"Replay did not match current records: {exc.fields!r}; rows={replayed!r}")

    def fulfill_onsite(self, order):
        self.register(order)
        self.verify(order)
        order.refresh_from_db()
        result = deliver_on_site(self.user, order.pk, {
            "operation_key": str(uuid4()), "expected_version": order.version,
        })
        order.refresh_from_db()
        return result


class InventoryAdjustmentPhase5Tests(Phase5Base):
    def test_adjustments_page_renders_structured_chinese_forms(self):
        permission_names = (
            "create_inventory_split", "create_inventory_gift", "resolve_damaged_record",
            "add_stockcount", "submit_costcorrection", "approve_stockcount",
            "approve_costcorrection", "view_stockcount", "view_costcorrection",
            "reject_costcorrection",
        )
        self.owner.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="crm", codename__in=permission_names,
        ))
        receipt = self.receive(quantity_value=4, price="3.000")

        self.client.force_login(self.owner)
        response = self.client.get("/adjustments/")

        self.assertEqual(response.status_code, 200)
        page = response.content.decode()
        self.assertNotIn("<textarea", page)
        self.assertIn('id="add-gift-line"', page)
        self.assertIn('id="add-count-line"', page)
        self.assertIn('id="correction-source"', page)
        self.assertIn("renderChangeSections", page)
        self.assertIn("operationKeySignature(currentUserId, scope, keyBody)", page)
        self.assertIn("{input_hash: correctionInputHash}", page)
        self.assertIn("loadCorrections({clearBlockedCommissionSuggestions: true})", page)
        self.assertIn("if (!refreshed) return;", page)
        self.assertIn("if (options.clearBlockedCommissionSuggestions) blockedCommissionSuggestions.clear();", page)
        self.assertIn("const fundingMessage = result.offline_transfer_performed", page)
        self.assertIn(": '本次无需线下资金处理。';", page)
        self.assertIn("countLineDisplay(row.status, line)", page)
        self.assertIn(f'"id": {receipt.pk}', page)
        self.assertIn("重核此单", page)
        self.assertIn("审批最新盘点影响", page)

    def test_correction_replay_does_not_touch_unrelated_holds_or_damage(self):
        source_receipt = self.receive(quantity_value=10, price="10.000")
        order = self.new_order(lines=[{
            "sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk,
            "quantity": 2, "sale_price": "20.000",
        }])
        unrelated_sku = self.new_sku(2)
        unrelated_receipt = self.receive(
            unrelated_sku, warehouse=self.second_warehouse, quantity_value=4, price="3.000",
            reserved=1, damaged=1,
        )
        unrelated_line = ReceiptLine.objects.get(receipt=unrelated_receipt)
        unrelated_balance = self.balance_for(unrelated_sku, self.second_warehouse)
        hold = InventoryHold.objects.get(receipt_line=unrelated_line)
        damage = DamagedRecord.objects.get(receipt_line=unrelated_line)
        unrelated_before = (
            unrelated_balance.pk, unrelated_balance.version, unrelated_balance.quantity_version,
            unrelated_balance.on_hand, unrelated_balance.reserved, unrelated_balance.damaged,
            unrelated_balance.cost_amount,
            hold.balance_id, hold.version, hold.initial_quantity, hold.remaining_quantity,
            damage.balance_id, damage.version, damage.original_quantity, damage.remaining_quantity,
        )
        sequence_before = BusinessControl.objects.get(pk=1).last_sequence

        preview = preview_cost_correction(self.user, self.correction_data(source_receipt, purchase_price="11.000"))
        submitted = submit_cost_correction(self.user, uuid4(), preview["preview_token"])
        correction = CostCorrection.objects.get(pk=submitted["cost_correction_id"])
        self.approve_correction(correction)

        unrelated_balance.refresh_from_db()
        hold.refresh_from_db()
        damage.refresh_from_db()
        self.assertEqual((unrelated_balance.pk, unrelated_balance.version, unrelated_balance.quantity_version,
                          unrelated_balance.on_hand, unrelated_balance.reserved, unrelated_balance.damaged,
                          unrelated_balance.cost_amount,
                          hold.balance_id, hold.version, hold.initial_quantity, hold.remaining_quantity,
                          damage.balance_id, damage.version, damage.original_quantity, damage.remaining_quantity),
                         unrelated_before)
        self.assertEqual(BusinessControl.objects.get(pk=1).last_sequence, sequence_before + 2)
        self.assertEqual(self.balance_for().cost_amount, Decimal("110.000"))
        self.assertIsNone(SalesOrderLine.objects.get(order=order, active=True).order_cost_amount)
        self.assert_replay()

    def test_cost_correction_responses_filter_order_scope_and_commission_permission(self):
        receipt = self.receive(quantity_value=5, price="10.000")
        payload = self.order_payload(lines=[{
            "sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk,
            "quantity": 2, "sale_price": "20.000",
        }])
        _, _, other_order = self.preview_and_confirm(payload, actor=self.sales)
        self.sales.user_permissions.add(
            Permission.objects.get(codename="submit_costcorrection"),
            Permission.objects.get(codename="view_all_sales_orders"),
            Permission.objects.get(codename="view_costcorrection"),
        )
        self.sales = type(self.sales).objects.get(pk=self.sales.pk)
        self.register(other_order)
        self.verify(other_order)
        other_order.refresh_from_db()
        deliver_on_site(self.user, other_order.pk, {
            "operation_key": str(uuid4()), "expected_version": other_order.version,
        })
        SalesOrder.objects.filter(pk=other_order.pk).update(salesperson_id=self.sales_two.pk)
        other_order.refresh_from_db()
        preview = preview_cost_correction(self.sales, self.correction_data(receipt, purchase_price="11.000"))
        preview_data = correction_service._load_token(preview["preview_token"], self.sales)
        self.assertEqual(preview["input_hash"], correction_service._operation_input_hash(preview_data))
        order_change = next(row for row in preview["changes"]["orders"] if row["order_id"] == other_order.pk)
        self.assertNotIn("commission_estimate", order_change["before"])
        self.assertNotIn("commission_estimate", order_change["after"])
        self.assertIn(other_order.pk, preview["impact"]["orders"])

        submitted = submit_cost_correction(self.sales, uuid4(), preview["preview_token"])
        correction_id = submitted["cost_correction_id"]
        detail = cost_correction_rows(self.sales, correction_id=correction_id)[0]["before_after"]
        stored = CostCorrection.objects.get(pk=correction_id).before_after
        self.assertIn(other_order.pk, [row["order_id"] for row in stored["changes"]["orders"]])
        self.assertNotIn("commission_estimate", detail["changes"]["orders"][0]["before"])
        self.assertNotIn("commission_estimate", detail["changes"]["orders"][0]["after"])
        self.client.force_login(self.sales)
        http_detail = self.client.get(f"/api/v1/cost-corrections/{correction_id}").json()["before_after"]
        http_list = self.client.get("/api/v1/cost-corrections").json()["items"][0]["before_after"]
        self.assertIn(other_order.pk, http_detail["impact"]["orders"])
        self.assertNotIn("commission_estimate", http_detail["changes"]["orders"][0]["before"])
        self.assertNotIn("commission_estimate", http_list["changes"]["orders"][0]["after"])

        self.sales.user_permissions.remove(Permission.objects.get(codename="view_all_sales_orders"))
        self.sales = type(self.sales).objects.get(pk=self.sales.pk)
        own_scope_detail = cost_correction_rows(self.sales, correction_id=correction_id)[0]["before_after"]
        self.assertNotIn(other_order.pk, [row["order_id"] for row in own_scope_detail["changes"].get("orders", [])])
        self.assertNotIn(other_order.pk, own_scope_detail["impact"]["orders"])
        own_scope_preview = preview_cost_correction(self.sales, self.correction_data(receipt, purchase_price="12.000"))
        self.assertNotIn(other_order.pk, [row["order_id"] for row in own_scope_preview["changes"].get("orders", [])])
        self.assertNotIn(other_order.pk, own_scope_preview["impact"]["orders"])
        http_detail_after_revoke = self.client.get(f"/api/v1/cost-corrections/{correction_id}").json()["before_after"]
        http_list_after_revoke = self.client.get("/api/v1/cost-corrections").json()["items"][0]["before_after"]
        self.assertNotIn(other_order.pk, http_detail_after_revoke["impact"]["orders"])
        self.assertNotIn(other_order.pk, http_list_after_revoke["impact"]["orders"])

    def test_cost_correction_http_error_fields_follow_order_and_commission_scope(self):
        self.receive(quantity_value=8, price="5.000")
        own_order = self.preview_and_confirm(self.order_payload(), actor=self.sales)[2]
        other_order = self.preview_and_confirm(self.order_payload(), actor=self.sales_two)[2]
        self.sales.user_permissions.add(
            Permission.objects.get(codename="submit_costcorrection"),
            Permission.objects.get(codename="view_costcorrection"),
        )
        self.sales = type(self.sales).objects.get(pk=self.sales.pk)
        self.client.force_login(self.sales)
        fields = {
            "orders": [own_order.pk, other_order.pk],
            "details": [
                {"order_id": own_order.pk, "commission_estimate": "1.000"},
                {"order_id": other_order.pk, "commission_estimate": "2.000"},
            ],
        }
        with patch("crm.views.preview_cost_correction", side_effect=OperationError("Invalid preview.", fields=fields)):
            response = self.client.post(
                "/api/v1/cost-corrections/preview", data=json.dumps({}), content_type="application/json",
            )

        self.assertEqual(response.status_code, 400)
        error_fields = response.json()["error"]["fields"]
        self.assertEqual(error_fields["orders"], [own_order.pk])
        self.assertEqual(error_fields["details"], [{"order_id": own_order.pk, "commission_estimate": "1.000"}])

    def test_stock_count_average_rounds_total_cost_once_for_occupancy_gain(self):
        self.preview_and_receive(self.receipt_payload(
            self.sku, quantity_value=3, price="0.333", freight="0.001",
        ))
        count_result = create_stock_count(self.user, {"reason": "Count six units", "lines": [{
            "warehouse_id": self.warehouse.pk, "sku_id": self.sku.pk,
            "observed_quantity": 6, "cost_mode": StockCountLine.CostMode.AVERAGE,
        }]}, uuid4())
        count = StockCount.objects.get(pk=count_result["stock_count_id"])
        line = count.lines.get()
        row = stock_count_rows(self.owner, count_id=count.pk)[0]["lines"][0]
        self.assertEqual(row["preview"], {"delta": 3, "effective_unit_cost": "0.333", "cost_delta": "1.000"})

        self.approve_count(count)
        line.refresh_from_db()
        balance = self.balance_for()
        self.assertEqual((line.delta, line.effective_unit_cost, line.cost_delta),
                         (3, Decimal("0.333"), Decimal("1.000")))
        self.assertEqual((balance.on_hand, balance.cost_amount), (6, Decimal("2.000")))
        self.assert_replay()

    def test_approved_count_snapshot_survives_later_purchase(self):
        _, received = self.preview_and_receive(self.receipt_payload(
            self.sku, quantity_value=3, price="0.333", freight="0.001",
        ))
        receipt = Receipt.objects.get(pk=received["receipt_id"])
        created = create_stock_count(self.user, {"reason": "Approved snapshot", "lines": [{
            "warehouse_id": self.warehouse.pk, "sku_id": self.sku.pk,
            "observed_quantity": 6, "cost_mode": StockCountLine.CostMode.AVERAGE,
        }]}, uuid4())
        count = StockCount.objects.get(pk=created["stock_count_id"])
        self.approve_count(count)
        line = count.lines.get()
        first = stock_count_rows(self.owner, count_id=count.pk)[0]["lines"][0]
        approved_values = (
            first["approved_delta"], first["approved_effective_unit_cost"], first["approved_cost_delta"],
        )
        self.assertEqual(approved_values, (3, "0.333", "1.000"))

        self.receive(quantity_value=1, price="2.000")
        after_purchase = stock_count_rows(self.owner, count_id=count.pk)[0]["lines"][0]
        self.assertEqual((after_purchase["approved_delta"], after_purchase["approved_effective_unit_cost"],
                          after_purchase["approved_cost_delta"]), approved_values)
        self.assertNotEqual(after_purchase["preview"]["delta"], after_purchase["approved_delta"])
        self.assertNotEqual(after_purchase["preview"]["cost_delta"], after_purchase["approved_cost_delta"])
        self.assertNotEqual(after_purchase["current_quantity_version"], after_purchase["captured_quantity_version"])

        preview = preview_cost_correction(self.user, self.correction_data(receipt, purchase_price="0.666"))
        self.assertTrue(preview["approval_required"])
        self.assertIn("counts", preview["changes"])
        correction_result = submit_cost_correction(self.user, uuid4(), preview["preview_token"])
        self.approve_correction(CostCorrection.objects.get(pk=correction_result["cost_correction_id"]))
        line.refresh_from_db()
        after_correction = stock_count_rows(self.owner, count_id=count.pk)[0]["lines"][0]
        corrected_values = (
            after_correction["approved_delta"], after_correction["approved_effective_unit_cost"],
            after_correction["approved_cost_delta"],
        )
        self.assertEqual(corrected_values, (line.delta, f"{line.effective_unit_cost:.3f}", f"{line.cost_delta:.3f}"))
        self.assertNotEqual(corrected_values, approved_values)
        self.assertNotEqual(after_correction["preview"]["delta"], after_correction["approved_delta"])
        self.assert_replay()

    def test_non_owner_superuser_page_hides_own_count_approval(self):
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save(update_fields=("is_superuser", "is_staff"))
        self.receive(quantity_value=2, price="1.000")
        create_stock_count(self.user, {"reason": "Admin own count", "lines": [{
            "warehouse_id": self.warehouse.pk, "sku_id": self.sku.pk, "observed_quantity": 2,
        }]}, uuid4())
        self.client.force_login(self.user)

        response = self.client.get("/adjustments/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_approve_count"])
        self.assertFalse(response.context["can_approve_own_count"])
        self.assertContains(response, "shouldShowCountApproval(canApproveCount, row.submitted_by, currentUserId, canApproveOwnCount)")

    def test_manual_stock_count_cost_requires_zero_on_hand_and_positive_gain(self):
        self.receive(quantity_value=2, price="0.000")
        payload = {"reason": "Keep true zero average", "lines": [{
            "warehouse_id": self.warehouse.pk, "sku_id": self.sku.pk,
            "observed_quantity": 1, "cost_mode": StockCountLine.CostMode.MANUAL,
            "manual_unit_cost": "1.000",
        }]}
        with self.assertRaises(OperationError) as raised:
            create_stock_count(self.user, payload, uuid4())
        self.assertIn("lines[0].cost_mode", raised.exception.fields)

        zero_cost_count = create_stock_count(self.user, {"reason": "True zero average", "lines": [{
            "warehouse_id": self.warehouse.pk, "sku_id": self.sku.pk, "observed_quantity": 3,
        }]}, uuid4())
        self.approve_count(StockCount.objects.get(pk=zero_cost_count["stock_count_id"]))
        self.assertEqual(self.balance_for().cost_amount, Decimal("0.000"))

        self.receive(quantity_value=1, price="0.000")
        create_inventory_gift(self.user, {"reason": "Clear zero-cost stock", "lines": [{
            "warehouse_id": self.warehouse.pk, "sku_id": self.sku.pk, "quantity": 4,
        }]}, uuid4())
        empty = create_stock_count(self.user, {"reason": "Untraced gain", "lines": [{
            "warehouse_id": self.warehouse.pk, "sku_id": self.sku.pk, "observed_quantity": 1,
            "cost_mode": StockCountLine.CostMode.MANUAL, "manual_unit_cost": "4.000",
        }]}, uuid4())
        self.approve_count(StockCount.objects.get(pk=empty["stock_count_id"]))
        self.assertEqual((self.balance_for().on_hand, self.balance_for().cost_amount), (1, Decimal("4.000")))
        self.assert_replay()

    def test_no_impact_correction_applies_directly_and_is_idempotent(self):
        receipt = self.receive(quantity_value=5, price="10.000")
        preview = preview_cost_correction(self.user, self.correction_data(receipt, purchase_price="11.000"))
        self.assertFalse(preview["approval_required"])
        operation_key = uuid4()
        submitted = submit_cost_correction(self.user, operation_key, preview["preview_token"])
        self.assertEqual(submit_cost_correction(self.user, operation_key, preview["preview_token"]), submitted)
        correction = CostCorrection.objects.get(pk=submitted["cost_correction_id"])
        self.assertEqual(correction.status, CostCorrection.Status.APPLIED)
        self.assertEqual(correction.reviewer_id, self.user.pk)
        self.assertEqual(self.balance_for().cost_amount, Decimal("55.000"))
        self.assert_replay()

    def test_split_gift_idempotency_and_transaction_rollback(self):
        self.receive(quantity_value=1, price="1000.000")
        target_sku = self.new_sku(2)
        self.receive(target_sku, warehouse=self.second_warehouse, quantity_value=5, price="40.000")
        data = {
            "source_warehouse_id": self.warehouse.pk,
            "source_sku_id": self.sku.pk,
            "source_quantity": 1,
            "target_warehouse_id": self.second_warehouse.pk,
            "target_sku_id": target_sku.pk,
            "target_quantity": 20,
            "reason": "Split synthetic box into retail units",
        }
        operation_key = uuid4()
        result = create_inventory_split(self.user, data, operation_key)
        self.assertEqual(result["transferred_cost"], "1000.000")
        self.assertEqual(create_inventory_split(self.user, data, operation_key), result)
        source = self.balance_for()
        target = self.balance_for(target_sku, self.second_warehouse)
        self.assertEqual((source.on_hand, source.cost_amount), (0, Decimal("0.000")))
        self.assertEqual((target.on_hand, target.cost_amount), (25, Decimal("1200.000")))
        split = SplitRecord.objects.get(pk=result["split_id"])
        self.assertEqual(split.transferred_cost, Decimal("1000.000"))
        with self.assertRaises(OperationError) as raised:
            create_inventory_split(self.user, {**data, "source_quantity": 1, "reason": "Overdraw"}, uuid4())
        self.assertEqual(raised.exception.code, "stock_conflict")

        gift_data = {"reason": "Synthetic customer gift", "lines": [{
            "warehouse_id": self.second_warehouse.pk, "sku_id": target_sku.pk, "quantity": 5,
        }]}
        gift_key = uuid4()
        gift_result = create_inventory_gift(self.user, gift_data, gift_key)
        self.assertEqual(create_inventory_gift(self.user, gift_data, gift_key), gift_result)
        gift = GiftRecord.objects.get(pk=gift_result["gift_id"])
        gift_line = GiftLine.objects.get(gift=gift)
        self.assertEqual(gift_line.cost_amount, Decimal("240.000"))
        target.refresh_from_db()
        self.assertEqual((target.on_hand, target.cost_amount), (20, Decimal("960.000")))
        self.assertFalse(SalesOrder.objects.exists())
        run_and_validate()

        rollback_sku = self.new_sku(3)
        self.receive(rollback_sku, quantity_value=1, price="50.000")
        before = (self.balance_for(rollback_sku).on_hand, self.balance_for(rollback_sku).cost_amount,
                  BusinessControl.objects.get(pk=1).last_sequence, SplitRecord.objects.count())
        rollback_data = {**data, "source_sku_id": rollback_sku.pk, "target_sku_id": target_sku.pk,
                         "source_quantity": 1, "target_quantity": 2}
        create_movement = InventoryMovement.objects.create
        calls = 0

        def fail_second_movement(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("synthetic movement write failure")
            return create_movement(*args, **kwargs)

        with patch("crm.inventory_adjustments.InventoryMovement.objects.create", side_effect=fail_second_movement):
            with self.assertRaises(RuntimeError):
                create_inventory_split(self.user, rollback_data, uuid4())
        self.assertEqual((self.balance_for(rollback_sku).on_hand, self.balance_for(rollback_sku).cost_amount,
                          BusinessControl.objects.get(pk=1).last_sequence, SplitRecord.objects.count()), before)

    def test_split_within_same_warehouse(self):
        self.receive(quantity_value=1, price="1000.000")
        target_sku = self.new_sku(2)

        result = create_inventory_split(self.user, {
            "source_warehouse_id": self.warehouse.pk,
            "source_sku_id": self.sku.pk,
            "source_quantity": 1,
            "target_warehouse_id": self.warehouse.pk,
            "target_sku_id": target_sku.pk,
            "target_quantity": 20,
            "reason": "Split within one warehouse",
        }, uuid4())

        self.assertEqual(result["transferred_cost"], "1000.000")
        source = self.balance_for()
        target = self.balance_for(target_sku)
        self.assertEqual((source.on_hand, source.cost_amount), (0, Decimal("0.000")))
        self.assertEqual((target.on_hand, target.cost_amount), (20, Decimal("1000.000")))
        self.assert_replay()

    def test_damage_exchange_refund_partial_retry_and_overflow(self):
        self.receive(quantity_value=20, price="10.000", damaged=2)
        balance = self.balance_for()
        record = DamagedRecord.objects.get(balance=balance)
        exchange = {
            "operation_key": str(uuid4()), "expected_version": record.version,
            "kind": DamageResolution.Kind.EXCHANGE, "quantity": 1,
        }
        exchanged = resolve_damaged_record(self.user, record.pk, exchange)
        self.assertEqual(resolve_damaged_record(self.user, record.pk, exchange), exchanged)
        record.refresh_from_db()
        balance.refresh_from_db()
        self.assertEqual((record.remaining_quantity, balance.on_hand, balance.damaged), (1, 20, 1))

        refund = resolve_damaged_record(self.user, record.pk, {
            "operation_key": str(uuid4()), "expected_version": record.version,
            "kind": DamageResolution.Kind.REFUND, "quantity": 1,
            "actual_refund": "8.000", "unreturned_freight": "2.000",
        })
        self.assertEqual(refund["deducted_cost"], "10.000")
        self.assertEqual(refund["difference"], "2.000")
        self.assertEqual(DamageResolution.objects.filter(damaged_record=record).count(), 2)
        record.refresh_from_db()
        balance.refresh_from_db()
        self.assertEqual((record.remaining_quantity, balance.on_hand, balance.damaged, balance.cost_amount),
                         (0, 19, 0, Decimal("190.000")))
        with self.assertRaises(OperationError) as raised:
            resolve_damaged_record(self.user, record.pk, {
                "operation_key": str(uuid4()), "expected_version": record.version,
                "kind": DamageResolution.Kind.REFUND, "quantity": 1, "actual_refund": "0.000",
            })
        self.assertEqual(raised.exception.code, "stock_conflict")
        run_and_validate()

    def test_stock_count_approval_quantity_anchor_and_owner_self_approval(self):
        self.receive(quantity_value=3, price="1.000")
        data = {"reason": "Count three units", "lines": [{
            "warehouse_id": self.warehouse.pk, "sku_id": self.sku.pk,
            "observed_quantity": 2,
        }]}
        first = create_stock_count(self.user, data, uuid4())
        count = StockCount.objects.get(pk=first["stock_count_id"])
        approval_key = uuid4()
        result = self.approve_count(count, key=approval_key)
        self.assertEqual(result["status"], StockCount.Status.APPROVED)
        count.refresh_from_db()
        self.assertEqual(count.approved_by_id, self.owner.pk)
        self.assertEqual(self.balance_for().on_hand, 2)

        # A later balance version alone is refreshable; physical quantity
        # changes remain stale even when a later receipt restores the count.
        stale_result = create_stock_count(self.user, {"reason": "Stale quantity anchor", "lines": [{
            "warehouse_id": self.warehouse.pk, "sku_id": self.sku.pk, "observed_quantity": 2,
        }]}, uuid4())
        stale = StockCount.objects.get(pk=stale_result["stock_count_id"])
        create_inventory_gift(self.user, {"reason": "One unit", "lines": [{
            "warehouse_id": self.warehouse.pk, "sku_id": self.sku.pk, "quantity": 1,
        }]}, uuid4())
        self.receive(quantity_value=1, price="1.000")
        versions = {str(line.pk): line.balance.version for line in stale.lines.select_related("balance")}
        with self.assertRaises(OperationError) as raised:
            approve_stock_count(self.owner, stale.pk, {
                "operation_key": str(uuid4()), "expected_version": stale.version,
                "balance_versions": versions,
            })
        self.assertEqual(raised.exception.code, "quantity_anchor_stale")

        self.user.user_permissions.add(Permission.objects.get(codename="approve_stockcount"))
        self.user = type(self.user).objects.get(pk=self.user.pk)
        self.assertTrue(self.user.has_perm("crm.approve_stockcount"))
        own_result = create_stock_count(self.user, {"reason": "My own count", "lines": [{
            "warehouse_id": self.warehouse.pk, "sku_id": self.sku.pk, "observed_quantity": 2,
        }]}, uuid4())
        own_count = StockCount.objects.get(pk=own_result["stock_count_id"])
        own_versions = {str(line.pk): line.balance.version for line in own_count.lines.select_related("balance")}
        with self.assertRaises(OperationError) as raised:
            approve_stock_count(self.user, own_count.pk, {
                "operation_key": str(uuid4()), "expected_version": own_count.version,
                "balance_versions": own_versions,
            })
        self.assertEqual(raised.exception.code, "self_approval_denied")
        approved = self.approve_count(own_count, actor=self.owner)
        self.assertEqual(approved["status"], StockCount.Status.APPROVED)
        run_and_validate()

    def test_stock_count_resubmission_makes_the_effective_submitter_own_the_review(self):
        self.receive(quantity_value=3, price="1.000")
        created = create_stock_count(self.user, {"reason": "First submitter", "lines": [{
            "warehouse_id": self.warehouse.pk, "sku_id": self.sku.pk, "observed_quantity": 2,
        }]}, uuid4())
        count = StockCount.objects.get(pk=created["stock_count_id"])
        first_submitter = self.user
        second_submitter = self.User.objects.create_user(username="count-resubmitter", password="test-only-password")
        second_submitter.groups.add(Group.objects.get(name=ROLE_WAREHOUSE))
        second_submitter.user_permissions.add(Permission.objects.get(content_type__app_label="crm", codename="approve_stockcount"))
        original_operations = list(BusinessOperation.objects.filter(stock_count=count).order_by("id").values_list("action", "actor_id"))
        balance = self.balance_for()
        before = (balance.on_hand, balance.version, InventoryMovement.objects.filter(stock_count_line__stock_count=count).count())

        resubmitted = revise_stock_count(second_submitter, count.pk, {
            "operation_key": str(uuid4()), "expected_version": count.version,
            "reason": "Second submitter", "lines": [{
                "warehouse_id": self.warehouse.pk, "sku_id": self.sku.pk, "observed_quantity": 2,
            }],
        })
        count.refresh_from_db()
        operations = list(BusinessOperation.objects.filter(stock_count=count).order_by("id").values_list("action", "actor_id"))
        self.assertEqual(count.submitted_by_id, second_submitter.pk)
        self.assertEqual((operations[0][1], operations[1][1]), (first_submitter.pk, second_submitter.pk))
        self.assertEqual(resubmitted["version"], count.version)
        with self.assertRaises(OperationError) as raised:
            self.approve_count(count, actor=second_submitter)
        self.assertEqual(raised.exception.code, "self_approval_denied")
        self.assertEqual(count.status, StockCount.Status.PENDING)
        self.assertIsNone(count.approved_by_id)
        balance.refresh_from_db()
        self.assertEqual((balance.on_hand, balance.version, InventoryMovement.objects.filter(stock_count_line__stock_count=count).count()), before)
        self.assertEqual(list(BusinessOperation.objects.filter(stock_count=count).order_by("id").values_list("action", "actor_id")), operations)
        approved = self.approve_count(count, actor=self.owner)
        self.assertEqual(approved["status"], StockCount.Status.APPROVED)
        self.assertEqual(original_operations[0][1], first_submitter.pk)
        run_and_validate()

    def test_stock_count_recheck_uses_patch_and_approves_only_rechecked_impact(self):
        second_sku = self.new_sku(2)
        self.receive_lines(self.warehouse, [(self.sku, 5, "1.000"), (second_sku, 3, "2.000")])
        created = create_stock_count(self.user, {"reason": "Recheck changed item", "lines": [
            {"warehouse_id": self.warehouse.pk, "sku_id": self.sku.pk, "observed_quantity": 5},
            {"warehouse_id": self.warehouse.pk, "sku_id": second_sku.pk, "observed_quantity": 3},
        ]}, uuid4())
        count = StockCount.objects.get(pk=created["stock_count_id"])
        first_line = count.lines.get(sku=self.sku)
        second_line = count.lines.get(sku=second_sku)
        first_quantity_version = first_line.captured_quantity_version
        second_quantity_version = second_line.captured_quantity_version
        create_inventory_gift(self.user, {"reason": "One item after count", "lines": [{
            "warehouse_id": self.warehouse.pk, "sku_id": self.sku.pk, "quantity": 1,
        }]}, uuid4())

        path = f"/api/v1/stock-counts/{count.pk}/update"
        self.client.force_login(self.user)
        patch_body = {
            "operation_key": str(uuid4()),
            "expected_version": count.version,
            "reason": count.reason,
            "lines": [{
                "warehouse_id": self.warehouse.pk,
                "sku_id": self.sku.pk,
                "observed_quantity": 3,
                "cost_mode": "average",
            }, {
                "warehouse_id": self.warehouse.pk,
                "sku_id": second_sku.pk,
                "observed_quantity": 3,
                "cost_mode": "average",
            }],
        }
        wrong_method = self.client.post(path, data=json.dumps(patch_body), content_type="application/json")
        self.assertEqual(wrong_method.status_code, 405)
        count.refresh_from_db()
        self.assertEqual(count.version, 1)
        self.assertEqual(count.lines.count(), 2)
        self.assertFalse(BusinessOperation.objects.filter(action="stock_count_resubmit").exists())

        revised = self.client.patch(path, data=json.dumps(patch_body), content_type="application/json")
        self.assertEqual(revised.status_code, 200, revised.content)
        self.assertEqual(revised.json()["version"], 2)
        count.refresh_from_db()
        first_line.refresh_from_db()
        second_line.refresh_from_db()
        self.assertEqual(count.lines.count(), 2)
        self.assertGreater(first_line.captured_quantity_version, first_quantity_version)
        self.assertEqual(second_line.captured_quantity_version, second_quantity_version)

        balance_versions = {
            str(line.pk): line.balance.version
            for line in count.lines.select_related("balance").order_by("id")
        }
        self.client.force_login(self.owner)
        approved = self.client.post(
            f"/api/v1/stock-counts/{count.pk}/approve",
            data=json.dumps({
                "operation_key": str(uuid4()),
                "expected_version": count.version,
                "balance_versions": balance_versions,
            }),
            content_type="application/json",
        )
        self.assertEqual(approved.status_code, 200, approved.content)
        self.assertEqual(approved.json()["status"], StockCount.Status.APPROVED)
        self.assertEqual(self.balance_for(self.sku).on_hand, 3)
        self.assertEqual(self.balance_for(second_sku).on_hand, 3)
        first_line.refresh_from_db()
        second_line.refresh_from_db()
        self.assertEqual(first_line.delta, -1)
        self.assertEqual(second_line.delta, 0)
        self.assertEqual(first_line.cost_delta, Decimal("-1.000"))
        self.assertEqual(second_line.cost_delta, Decimal("0.000"))
        self.assert_replay()

    def test_cost_correction_replays_order_and_delivery_costs(self):
        receipt = self.receive(quantity_value=10, price="100.000")
        order = self.new_order(lines=[{
            "sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk,
            "quantity": 5, "sale_price": "300.000",
        }])
        self.receive(quantity_value=10, price="200.000")
        self.register(order)
        self.verify(order)
        order.refresh_from_db()
        deliver_on_site(self.user, order.pk, {
            "operation_key": str(uuid4()), "expected_version": order.version,
        })
        self.assert_replay()

        preview = preview_cost_correction(self.user, self.correction_data(
            receipt, purchase_price="110.000",
        ))
        self.assertTrue(preview["approval_required"])
        correction = submit_cost_correction(self.user, uuid4(), preview["preview_token"])
        document = CostCorrection.objects.get(pk=correction["cost_correction_id"])
        self.approve_correction(document)
        order.refresh_from_db()
        line = SalesOrderLine.objects.get(order=order, active=True)
        balance = self.balance_for()
        fulfillment = BusinessOperation.objects.get(sales_order=order, action="sales_order_onsite_delivery")
        self.assertIsNone(line.unit_cost_snapshot)
        self.assertEqual(line.order_cost_amount, Decimal("775.000"))
        self.assertEqual(line.cost_mode, "actual_outflow")
        self.assertEqual(order.cost_amount, Decimal("775.000"))
        self.assertEqual(order.margin_amount, Decimal("725.000"))
        self.assertEqual((balance.on_hand, balance.cost_amount), (15, Decimal("2325.000")))
        self.assertEqual(fulfillment.result["actual_inventory_cost"], "775.000")
        self.assertEqual(PaymentRecord.objects.get(order=order).status, PaymentRecord.Status.PASSED)
        run_and_validate()

    def test_cross_warehouse_split_correction_updates_downstream_sale(self):
        source_receipt = self.receive(quantity_value=1, price="1000.000")
        target_sku = self.new_sku(2)
        split_result = create_inventory_split(self.user, {
            "source_warehouse_id": self.warehouse.pk, "source_sku_id": self.sku.pk, "source_quantity": 1,
            "target_warehouse_id": self.second_warehouse.pk, "target_sku_id": target_sku.pk,
            "target_quantity": 20, "reason": "Split to retail units",
        }, uuid4())
        order = self.new_order(lines=[{
            "sku_id": target_sku.pk, "warehouse_id": self.second_warehouse.pk,
            "quantity": 10, "sale_price": "100.000",
        }])
        self.register(order)
        self.verify(order)
        order.refresh_from_db()
        deliver_on_site(self.user, order.pk, {
            "operation_key": str(uuid4()), "expected_version": order.version,
        })
        run_and_validate()

        preview = preview_cost_correction(self.user, self.correction_data(
            source_receipt, purchase_price="1200.000",
        ))
        self.assertTrue(preview["approval_required"])
        correction = submit_cost_correction(self.user, uuid4(), preview["preview_token"])
        self.approve_correction(CostCorrection.objects.get(pk=correction["cost_correction_id"]))
        order.refresh_from_db()
        line = SalesOrderLine.objects.get(order=order, active=True)
        source = self.balance_for()
        target = self.balance_for(target_sku, self.second_warehouse)
        split = SplitRecord.objects.get(pk=split_result["split_id"])
        movement = InventoryMovement.objects.get(sales_order=order, event_kind="order_outbound")
        self.assertEqual((source.on_hand, source.cost_amount), (0, Decimal("0.000")))
        self.assertEqual((target.on_hand, target.cost_amount), (10, Decimal("600.000")))
        self.assertEqual(split.transferred_cost, Decimal("1200.000"))
        self.assertIsNone(line.unit_cost_snapshot)
        self.assertEqual(line.order_cost_amount, Decimal("600.000"))
        self.assertEqual(movement.cost_delta, Decimal("-600.000"))
        self.assertEqual((order.status, line.quantity, line.unit_price),
                         (SalesOrder.Status.COMPLETED, 10, Decimal("100.000")))
        run_and_validate()

    def test_historical_restriction_release_passes_but_real_negative_stock_fails(self):
        held_receipt = self.receive(quantity_value=100, price="1.000", reserved=80)
        hold = InventoryHold.objects.get(receipt_line__receipt=held_receipt)
        release_hold(self.user, hold.pk, {
            "operation_key": str(uuid4()), "expected_version": hold.version, "quantity": 80,
        })
        preview = preview_cost_correction(self.user, self.correction_data(
            held_receipt, quantity=60,
        ))
        self.assertIn("preview_token", preview)
        correction = submit_cost_correction(self.user, uuid4(), preview["preview_token"])
        if correction["approval_required"]:
            self.approve_correction(CostCorrection.objects.get(pk=correction["cost_correction_id"]))
        self.assertEqual(self.balance_for().on_hand, 60)
        self.assertEqual(
            InventoryHold.objects.filter(receipt_line__receipt=held_receipt).count(), 1,
            list(InventoryHold.objects.filter(receipt_line__receipt=held_receipt).values(
                "id", "initial_quantity", "remaining_quantity", "receipt_line_id", "type",
            )),
        )
        run_and_validate()

        outflow_receipt = self.receive(self.new_sku(3), quantity_value=100, price="1.000")
        outflow_sku = ReceiptLine.objects.get(receipt=outflow_receipt).sku
        order = self.new_order(lines=[{
            "sku_id": outflow_sku.pk, "warehouse_id": self.warehouse.pk,
            "quantity": 80, "sale_price": "2.000",
        }])
        self.register(order)
        self.verify(order)
        order.refresh_from_db()
        deliver_on_site(self.user, order.pk, {
            "operation_key": str(uuid4()), "expected_version": order.version,
        })
        before = (self.balance_for(outflow_sku).on_hand, self.balance_for(outflow_sku).cost_amount,
                  ReceiptLine.objects.get(receipt=outflow_receipt).quantity)
        with self.assertRaises(OperationError):
            preview_cost_correction(self.user, self.correction_data(outflow_receipt, quantity=60))
        self.assertEqual((self.balance_for(outflow_sku).on_hand, self.balance_for(outflow_sku).cost_amount,
                          ReceiptLine.objects.get(receipt=outflow_receipt).quantity), before)
        run_and_validate()

    def test_opening_count_cost_supplement_is_approved_with_correction(self):
        content = opening_workbook([product_row()], [stock_row(quantity=10, cost="100.000")])
        preview = preview_opening(self.user, content, "synthetic-opening.xlsx")
        self.assertTrue(preview["ready"])
        opened = confirm_opening(self.user, uuid4(), preview["preview_token"])
        batch_id = opened["opening_batch_id"]
        count_result = create_stock_count(self.user, {"reason": "Count five units", "lines": [{
            "warehouse_id": self.warehouse.pk, "sku_id": self.sku.pk,
            "observed_quantity": 5, "cost_mode": StockCountLine.CostMode.AVERAGE,
        }]}, uuid4())
        count = StockCount.objects.get(pk=count_result["stock_count_id"])
        self.approve_count(count)
        line = StockCountLine.objects.get(stock_count=count)
        self.assertEqual((line.delta, line.cost_delta), (-5, Decimal("-500.000")))

        data = {"opening_batch_id": batch_id, "reason": "Opening quantity was overstated",
                "lines": [{"line_number": 1, "quantity": 0}]}
        missing = preview_cost_correction(self.user, data)
        self.assertEqual(missing["status"], "count_cost_required")
        supplemented = preview_cost_correction(self.user, {
            **data,
            "supplemental_count_costs": [{"count_line_id": line.pk, "unit_cost": "7.000"}],
        })
        correction = submit_cost_correction(self.user, uuid4(), supplemented["preview_token"])
        document = CostCorrection.objects.get(pk=correction["cost_correction_id"])
        self.approve_correction(document)
        balance = self.balance_for()
        line.refresh_from_db()
        self.assertEqual((balance.on_hand, balance.cost_amount), (5, Decimal("35.000")))
        self.assertEqual((line.cost_mode, line.delta, line.cost_delta, line.supplemental_unit_cost),
                         (StockCountLine.CostMode.AVERAGE, 5, Decimal("35.000"), Decimal("7.000")))
        self.assertEqual(line.supplement_approval_id, document.pk)
        run_and_validate()

    def test_corrected_purchase_recalculates_approved_count(self):
        receipt = self.receive(quantity_value=100, price="10.000")
        count_result = create_stock_count(self.user, {"reason": "Count 95 units", "lines": [{
            "warehouse_id": self.warehouse.pk, "sku_id": self.sku.pk, "observed_quantity": 95,
        }]}, uuid4())
        count = StockCount.objects.get(pk=count_result["stock_count_id"])
        self.approve_count(count)
        line = StockCountLine.objects.get(stock_count=count)
        self.assertEqual((line.delta, line.cost_delta), (-5, Decimal("-50.000")))

        preview = preview_cost_correction(self.user, self.correction_data(receipt, quantity=90))
        self.assertTrue(preview["approval_required"])
        self.assertIn("counts", preview["changes"])
        result = submit_cost_correction(self.user, uuid4(), preview["preview_token"])
        self.approve_correction(CostCorrection.objects.get(pk=result["cost_correction_id"]))
        line.refresh_from_db()
        balance = self.balance_for()
        self.assertEqual((line.observed_quantity, line.delta, line.cost_delta), (95, 5, Decimal("50.000")))
        self.assertEqual((balance.on_hand, balance.cost_amount), (95, Decimal("950.000")))
        self.assert_replay()

    def test_manual_count_direction_flip_uses_historical_average_for_the_loss(self):
        receipt = self.receive(quantity_value=1, price="80.000")
        create_inventory_gift(self.user, {"reason": "Clear one unit", "lines": [{
            "warehouse_id": self.warehouse.pk, "sku_id": self.sku.pk, "quantity": 1,
        }]}, uuid4())
        created = create_stock_count(self.user, {"reason": "Manual positive gain before correction", "lines": [{
            "warehouse_id": self.warehouse.pk, "sku_id": self.sku.pk,
            "observed_quantity": 1, "cost_mode": StockCountLine.CostMode.MANUAL, "manual_unit_cost": "100.000",
        }]}, uuid4())
        count = StockCount.objects.get(pk=created["stock_count_id"])
        self.approve_count(count)
        line = StockCountLine.objects.get(stock_count=count)
        self.assertEqual((line.delta, line.cost_delta), (1, Decimal("100.000")))

        preview = preview_cost_correction(self.user, self.correction_data(receipt, quantity=3))
        result = submit_cost_correction(self.user, uuid4(), preview["preview_token"])
        self.approve_correction(CostCorrection.objects.get(pk=result["cost_correction_id"]))

        line.refresh_from_db()
        balance = self.balance_for()
        self.assertEqual((line.delta, line.cost_delta, line.effective_unit_cost), (-1, Decimal("-80.000"), Decimal("80.000")))
        self.assertEqual((line.cost_mode, line.manual_unit_cost), (StockCountLine.CostMode.MANUAL, Decimal("100.000")))
        self.assertEqual((balance.on_hand, balance.cost_amount), (1, Decimal("80.000")))
        self.assert_replay()

    def test_stale_commission_approval_is_not_carried_forward_by_cost_correction(self):
        receipt = self.receive(quantity_value=10, price="10.000")
        order = self.new_order(lines=[{
            "sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk,
            "quantity": 2, "sale_price": "20.000",
        }])
        self.fulfill_onsite(order)
        order.refresh_from_db()
        review_commission(self.owner, order.pk, {"operation_key": str(uuid4()), "expected_version": order.version})
        review = CommissionReview.objects.get(order=order)
        first_earned_at = review.first_earned_at

        order.refresh_from_db()
        update_outbound_freight(self.user, order.pk, {
            "operation_key": str(uuid4()), "expected_version": order.version,
            "outbound_freights": [{"line_id": order.lines.get(active=True).pk, "amount": "10.000"}],
        })
        order.refresh_from_db()
        stale_amount = review.approved_amount
        stale_version = review.approved_amount_version
        self.assertLess(stale_version, order.amount_version)
        preview = preview_cost_correction(self.user, self.correction_data(receipt, purchase_price="11.000"))
        result = submit_cost_correction(self.user, uuid4(), preview["preview_token"])
        self.approve_correction(CostCorrection.objects.get(pk=result["cost_correction_id"]))

        order.refresh_from_db()
        review.refresh_from_db()
        self.assertEqual((review.approved_amount, review.approved_amount_version), (stale_amount, stale_version))
        self.assertEqual(review.first_earned_at, first_earned_at)
        self.assertFalse(CommissionAdjustment.objects.filter(order=order, kind=CommissionAdjustment.Kind.COST_CORRECTION).exists())
        re_review = review_commission(self.owner, order.pk, {"operation_key": str(uuid4()), "expected_version": order.version})
        review.refresh_from_db()
        self.assertEqual(re_review["delta"], "-1.200")
        self.assertEqual(review.approved_amount, Decimal("0.800"))
        self.assertEqual(review.approved_amount_version, order.amount_version)
        self.assertEqual(review.first_earned_at, first_earned_at)
        self.assertEqual(CommissionAdjustment.objects.filter(order=order, kind=CommissionAdjustment.Kind.COST_CORRECTION).count(), 0)

    def test_repeated_corrections_preserve_approved_commission_until_each_explicit_decision(self):
        receipt = self.receive(quantity_value=10, price="10.000")
        order = self.new_order(lines=[{
            "sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk,
            "quantity": 5, "sale_price": "20.000",
        }])
        self.register(order)
        self.verify(order)
        order.refresh_from_db()
        deliver_on_site(self.user, order.pk, {
            "operation_key": str(uuid4()), "expected_version": order.version,
        })
        order.refresh_from_db()
        review_commission(self.owner, order.pk, {
            "operation_key": str(uuid4()), "expected_version": order.version,
        })
        review = CommissionReview.objects.get(order=order)
        first_earned_at = review.first_earned_at
        self.assertEqual(review.approved_amount, Decimal("5.000"))

        previous_approved = Decimal("5.000")
        for price, expected in (("11.000", Decimal("4.500")), ("12.000", Decimal("4.000"))):
            preview = preview_cost_correction(self.user, self.correction_data(receipt, purchase_price=price))
            self.assertTrue(preview["approval_required"])
            result = submit_cost_correction(self.user, uuid4(), preview["preview_token"])
            correction = CostCorrection.objects.get(pk=result["cost_correction_id"])
            self.approve_correction(correction)
            correction.refresh_from_db()
            order.refresh_from_db()
            review.refresh_from_db()
            suggestion = correction.before_after["commission_adjustment_suggestions"][0]
            self.assertEqual(review.approved_amount, previous_approved)
            self.assertEqual(review.approved_amount_version, suggestion["expected_approved_amount_version"])
            self.assertEqual(order.commission_estimate, expected)
            self.assertEqual(
                confirm_cost_correction_commission_decision(self.owner, order.pk, {
                    "operation_key": str(uuid4()),
                    "cost_correction_id": correction.pk,
                    "expected_amount_version": suggestion["expected_amount_version"],
                    "expected_approved_amount_version": suggestion["expected_approved_amount_version"],
                    "decision": "adjust",
                })["approved_amount"],
                f"{expected:.3f}",
            )
            order.refresh_from_db()
            review.refresh_from_db()
            self.assertEqual(review.approved_amount, expected)
            previous_approved = expected
            self.assertEqual(review.first_earned_at, first_earned_at)
            self.assertEqual(review.approved_amount_version, order.amount_version)
            self.assertEqual(PaymentRecord.objects.get(order=order).status, PaymentRecord.Status.PASSED)

        adjustments = list(CommissionAdjustment.objects.filter(
            order=order, kind=CommissionAdjustment.Kind.COST_CORRECTION,
        ).order_by("id"))
        self.assertEqual([row.delta for row in adjustments], [Decimal("-0.500"), Decimal("-0.500")])
        self.assertEqual(
            sum((row.delta for row in CommissionAdjustment.objects.filter(order=order)), Decimal("0.000")),
            Decimal("4.000"),
        )
        self.assert_replay()

    def _unresolved_cost_commission_suggestion(self):
        receipt = self.receive(quantity_value=10, price="10.000")
        order = self.new_order(lines=[{
            "sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk,
            "quantity": 5, "sale_price": "20.000",
        }])
        self.fulfill_onsite(order)
        review_data = {
            "operation_key": str(uuid4()), "expected_version": order.version,
        }
        review_result = review_commission(self.owner, order.pk, review_data)
        review = CommissionReview.objects.get(order=order)
        preview = preview_cost_correction(self.user, self.correction_data(receipt, purchase_price="11.000"))
        result = submit_cost_correction(self.user, uuid4(), preview["preview_token"])
        correction_a = CostCorrection.objects.get(pk=result["cost_correction_id"])
        self.approve_correction(correction_a)
        correction_a.refresh_from_db()
        order.refresh_from_db()
        review.refresh_from_db()
        suggestion_a = correction_a.before_after["commission_adjustment_suggestions"][0]
        return receipt, order, review, correction_a, suggestion_a, review_data, review_result

    def test_correction_list_exposes_commission_suggestions_only_to_owner(self):
        _, _, _, correction, suggestion, _, _ = self._unresolved_cost_commission_suggestion()

        self.client.force_login(self.owner)
        owner_response = self.client.get("/api/v1/cost-corrections")
        self.assertEqual(owner_response.status_code, 200, owner_response.content)
        owner_row = next(item for item in owner_response.json()["items"] if item["id"] == correction.pk)
        self.assertEqual(owner_row["before_after"]["commission_adjustment_suggestions"], [suggestion])

        reviewer = self.User.objects.create_user(username="synthetic-commission-reviewer", password="test-only-password")
        reviewer.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="crm",
            codename__in=("view_costcorrection", "view_all_sales_orders", "view_all_commissions", "review_commission"),
        ))
        reviewer_client = Client()
        reviewer_client.force_login(reviewer)
        reviewer_response = reviewer_client.get("/api/v1/cost-corrections")
        self.assertEqual(reviewer_response.status_code, 200, reviewer_response.content)
        reviewer_row = next(item for item in reviewer_response.json()["items"] if item["id"] == correction.pk)
        self.assertNotIn("commission_adjustment_suggestions", reviewer_row["before_after"])

    def _apply_next_purchase_price_correction(self, receipt, price="12.000"):
        preview = preview_cost_correction(self.user, self.correction_data(receipt, purchase_price=price))
        result = submit_cost_correction(self.user, uuid4(), preview["preview_token"])
        correction = CostCorrection.objects.get(pk=result["cost_correction_id"])
        self.approve_correction(correction)
        correction.refresh_from_db()
        return correction

    def test_unresolved_cost_suggestion_is_replaced_by_next_contiguous_correction(self):
        receipt, order, review, correction_a, suggestion_a, _, _ = self._unresolved_cost_commission_suggestion()
        suggestion_b_correction = self._apply_next_purchase_price_correction(receipt)
        suggestion_b = suggestion_b_correction.before_after["commission_adjustment_suggestions"][0]
        order.refresh_from_db()
        review.refresh_from_db()

        self.assertEqual(suggestion_b["source_amount_version"], suggestion_a["expected_amount_version"])
        self.assertEqual(suggestion_b["expected_amount_version"], order.amount_version)
        self.assertEqual(suggestion_b["approved_amount"], "5.000")
        self.assertEqual(
            suggestion_b["expected_approved_amount_version"],
            suggestion_a["expected_approved_amount_version"],
        )
        self.assertEqual(suggestion_b["target_amount"], "4.000")
        self.assertEqual(review.approved_amount, Decimal("5.000"))
        self.assertEqual(review.approved_amount_version, suggestion_a["expected_approved_amount_version"])

        with self.assertRaises(OperationError) as raised:
            confirm_cost_correction_commission_decision(self.owner, order.pk, {
                "operation_key": str(uuid4()),
                "cost_correction_id": correction_a.pk,
                "expected_amount_version": suggestion_a["expected_amount_version"],
                "expected_approved_amount_version": suggestion_a["expected_approved_amount_version"],
                "decision": "adjust",
            })
        self.assertEqual(raised.exception.code, "stale_suggestion")

        data = {
            "operation_key": str(uuid4()),
            "cost_correction_id": suggestion_b_correction.pk,
            "expected_amount_version": suggestion_b["expected_amount_version"],
            "expected_approved_amount_version": suggestion_b["expected_approved_amount_version"],
            "decision": "adjust",
        }
        result = confirm_cost_correction_commission_decision(self.owner, order.pk, data)
        self.assertEqual(confirm_cost_correction_commission_decision(self.owner, order.pk, data), result)
        self.assertEqual(result["approved_amount_before"], "5.000")
        self.assertEqual(result["approved_amount"], "4.000")
        self.assertTrue(result["offline_transfer_required"])
        self.assertFalse(result["offline_transfer_performed"])
        review.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(review.approved_amount, Decimal("4.000"))
        self.assertEqual(review.approved_amount_version, order.amount_version)
        self.assertEqual(CommissionAdjustment.objects.filter(
            order=order, kind=CommissionAdjustment.Kind.COST_CORRECTION,
        ).count(), 1)

    def test_unresolved_cost_suggestion_can_be_kept_idempotently_after_next_correction(self):
        receipt, order, review, _, _, _, _ = self._unresolved_cost_commission_suggestion()
        correction_b = self._apply_next_purchase_price_correction(receipt)
        suggestion_b = correction_b.before_after["commission_adjustment_suggestions"][0]
        data = {
            "operation_key": str(uuid4()),
            "cost_correction_id": correction_b.pk,
            "expected_amount_version": suggestion_b["expected_amount_version"],
            "expected_approved_amount_version": suggestion_b["expected_approved_amount_version"],
            "decision": "keep",
        }

        result = confirm_cost_correction_commission_decision(self.owner, order.pk, data)
        self.assertEqual(confirm_cost_correction_commission_decision(self.owner, order.pk, data), result)
        self.assertEqual(result["approved_amount_before"], "5.000")
        self.assertEqual(result["approved_amount"], "5.000")
        self.assertFalse(result["offline_transfer_required"])
        self.assertFalse(result["offline_transfer_performed"])
        self.assertIsNone(result["adjustment_id"])
        review.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(review.approved_amount, Decimal("5.000"))
        self.assertEqual(review.approved_amount_version, order.amount_version)
        self.assertEqual(CommissionAdjustment.objects.filter(
            order=order, kind=CommissionAdjustment.Kind.COST_CORRECTION,
        ).count(), 0)

    def test_later_correction_does_not_carry_suggestion_across_freight_review(self):
        receipt, order, review, _, suggestion_a, _, _ = self._unresolved_cost_commission_suggestion()
        update_outbound_freight(self.user, order.pk, {
            "operation_key": str(uuid4()), "expected_version": order.version,
            "outbound_freights": [{"line_id": order.lines.get(active=True).pk, "amount": "1.000"}],
        })
        order.refresh_from_db()
        self.assertGreater(order.amount_version, suggestion_a["expected_amount_version"])

        correction_b = self._apply_next_purchase_price_correction(receipt)
        self.assertEqual(correction_b.before_after["commission_adjustment_suggestions"], [])
        review.refresh_from_db()
        order.refresh_from_db()
        self.assertLess(review.approved_amount_version, order.amount_version)
        self.assertFalse(CommissionAdjustment.objects.filter(
            order=order, kind=CommissionAdjustment.Kind.COST_CORRECTION,
        ).exists())

        result = review_commission(self.owner, order.pk, {
            "operation_key": str(uuid4()), "expected_version": order.version,
        })
        review.refresh_from_db()
        adjustment = CommissionAdjustment.objects.get(pk=result["adjustment_id"])
        self.assertEqual(adjustment.kind, CommissionAdjustment.Kind.REREVIEW)
        self.assertEqual(review.approved_amount, order.commission_estimate)
        self.assertEqual(review.approved_amount_version, order.amount_version)

    def test_current_cost_suggestion_blocks_legacy_review_without_breaking_replay(self):
        _, order, review, _, _, review_data, review_result = self._unresolved_cost_commission_suggestion()
        endpoint = f"/api/v1/orders/{order.pk}/review-commission"
        self.client.force_login(self.owner)
        replay = self.client.post(endpoint, data=review_data, content_type="application/json")
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertEqual(replay.json(), review_result)

        order.refresh_from_db()
        review.refresh_from_db()
        before = (
            order.version,
            order.amount_version,
            review.approved_amount,
            review.approved_amount_version,
            BusinessOperation.objects.filter(sales_order=order).count(),
            tuple(CommissionAdjustment.objects.filter(order=order).values_list("pk", "kind", "delta")),
        )
        self.sales.user_permissions.add(Permission.objects.get(codename="review_commission"))
        single_permission_user = self.User.objects.get(pk=self.sales.pk)
        self.sales.user_permissions.add(Permission.objects.get(codename="approve_costcorrection"))
        dual_permission_user = self.User.objects.get(pk=self.sales.pk)
        administrator = self.User.objects.create_user(username="legacy-review-admin", password="test-only-password")
        administrator.is_superuser = True
        administrator.save(update_fields=("is_superuser",))

        for actor in (single_permission_user, dual_permission_user, self.owner, administrator):
            self.client.force_login(actor)
            response = self.client.post(endpoint, data={
                "operation_key": str(uuid4()), "expected_version": order.version,
            }, content_type="application/json")
            self.assertEqual(response.status_code, 409, response.content)
            self.assertEqual(response.json()["error"]["code"], "cost_correction_decision_required")
            order.refresh_from_db()
            review.refresh_from_db()
            current = (
                order.version,
                order.amount_version,
                review.approved_amount,
                review.approved_amount_version,
                BusinessOperation.objects.filter(sales_order=order).count(),
                tuple(CommissionAdjustment.objects.filter(order=order).values_list("pk", "kind", "delta")),
            )
            self.assertEqual(current, before)

    def test_cost_commission_decision_requires_owner_or_superuser_and_is_idempotent(self):
        receipt = self.receive(quantity_value=10, price="10.000")
        order = self.new_order(lines=[{
            "sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk,
            "quantity": 2, "sale_price": "20.000",
        }])
        self.fulfill_onsite(order)
        order.refresh_from_db()
        review_commission(self.owner, order.pk, {
            "operation_key": str(uuid4()), "expected_version": order.version,
        })
        preview = preview_cost_correction(self.user, self.correction_data(receipt, purchase_price="11.000"))
        result = submit_cost_correction(self.user, uuid4(), preview["preview_token"])
        correction = CostCorrection.objects.get(pk=result["cost_correction_id"])
        self.approve_correction(correction)
        correction.refresh_from_db()
        suggestion = correction.before_after["commission_adjustment_suggestions"][0]
        data = {
            "operation_key": str(uuid4()),
            "cost_correction_id": correction.pk,
            "expected_amount_version": suggestion["expected_amount_version"],
            "expected_approved_amount_version": suggestion["expected_approved_amount_version"],
            "decision": "adjust",
        }

        self.sales.user_permissions.add(
            Permission.objects.get(codename="review_commission"),
            Permission.objects.get(codename="approve_costcorrection"),
        )
        self.sales = self.User.objects.get(pk=self.sales.pk)
        endpoint = f"/api/v1/orders/{order.pk}/commission-adjustment-confirm"
        self.client.force_login(self.sales)
        denied = self.client.post(endpoint, data=data, content_type="application/json")
        self.assertEqual(denied.status_code, 403, denied.content)
        self.assertEqual(denied.json()["error"]["code"], "permission_denied")

        administrator = self.User.objects.create_user(username="synthetic-cost-admin", password="test-only-password")
        administrator.is_superuser = True
        administrator.save(update_fields=("is_superuser",))
        self.client.force_login(administrator)
        first_response = self.client.post(endpoint, data=data, content_type="application/json")
        replay_response = self.client.post(endpoint, data=data, content_type="application/json")
        self.assertEqual(first_response.status_code, 200, first_response.content)
        self.assertEqual(replay_response.status_code, 200, replay_response.content)
        first = first_response.json()
        replay = replay_response.json()
        self.assertEqual(first, replay)
        self.assertEqual(first["decision"], "adjust")
        self.assertEqual(first["delta_amount"], "-0.200")
        self.assertIsNotNone(first["adjustment_id"])
        self.assertTrue(first["offline_transfer_required"])
        self.assertFalse(first["offline_transfer_performed"])
        order.refresh_from_db()
        review = CommissionReview.objects.get(order=order)
        self.assertEqual(review.approved_amount, Decimal("1.800"))
        self.assertEqual(review.approved_amount_version, order.amount_version)
        adjustment = CommissionAdjustment.objects.get(
            order=order, kind=CommissionAdjustment.Kind.COST_CORRECTION,
        )
        self.assertEqual(adjustment.delta, Decimal("-0.200"))

    def test_cost_commission_decision_rejects_a_suggestion_invalidated_by_freight(self):
        receipt = self.receive(quantity_value=10, price="10.000")
        order = self.new_order(lines=[{
            "sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk,
            "quantity": 2, "sale_price": "20.000",
        }])
        self.fulfill_onsite(order)
        order.refresh_from_db()
        review_commission(self.owner, order.pk, {
            "operation_key": str(uuid4()), "expected_version": order.version,
        })
        preview = preview_cost_correction(self.user, self.correction_data(receipt, purchase_price="11.000"))
        result = submit_cost_correction(self.user, uuid4(), preview["preview_token"])
        correction = CostCorrection.objects.get(pk=result["cost_correction_id"])
        self.approve_correction(correction)
        correction.refresh_from_db()
        suggestion = correction.before_after["commission_adjustment_suggestions"][0]
        order.refresh_from_db()
        update_outbound_freight(self.user, order.pk, {
            "operation_key": str(uuid4()), "expected_version": order.version,
            "outbound_freights": [{"line_id": order.lines.get(active=True).pk, "amount": "1.000"}],
        })
        with self.assertRaises(OperationError) as raised:
            confirm_cost_correction_commission_decision(self.owner, order.pk, {
                "operation_key": str(uuid4()),
                "cost_correction_id": correction.pk,
                "expected_amount_version": suggestion["expected_amount_version"],
                "expected_approved_amount_version": suggestion["expected_approved_amount_version"],
                "decision": "adjust",
            })
        self.assertEqual(raised.exception.code, "stale_suggestion")
        order.refresh_from_db()
        review = CommissionReview.objects.get(order=order)
        self.assertLess(review.approved_amount_version, order.amount_version)

    def test_receipt_header_correction_covers_both_warehouses_and_existing_sku_references(self):
        sku_y = self.new_sku(2)
        sku_z = self.new_sku(3)
        for warehouse, price in ((self.warehouse, "10.000"), (self.second_warehouse, "20.000")):
            self.receive(sku=self.sku, warehouse=warehouse, quantity_value=10, price=price)
            self.receive(sku=sku_y, warehouse=warehouse, quantity_value=10, price=price)
        source = self.receive_lines(self.warehouse, [
            (self.sku, 10, "30.000"), (sku_y, 10, "40.000"),
        ])

        first_order = self.new_order(lines=[
            {"sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk, "quantity": 5, "sale_price": "100.000"},
            {"sku_id": sku_y.pk, "warehouse_id": self.warehouse.pk, "quantity": 5, "sale_price": "100.000"},
        ])
        second_order = self.new_order(lines=[
            {"sku_id": self.sku.pk, "warehouse_id": self.second_warehouse.pk, "quantity": 5, "sale_price": "100.000"},
            {"sku_id": sku_y.pk, "warehouse_id": self.second_warehouse.pk, "quantity": 5, "sale_price": "100.000"},
        ])
        self.fulfill_onsite(first_order)
        self.fulfill_onsite(second_order)
        create_inventory_split(self.user, {
            "source_warehouse_id": self.warehouse.pk, "source_sku_id": self.sku.pk, "source_quantity": 2,
            "target_warehouse_id": self.second_warehouse.pk, "target_sku_id": sku_z.pk,
            "target_quantity": 2, "reason": "Split remaining W1 inventory",
        }, uuid4())
        target_order = self.new_order(lines=[{
            "sku_id": sku_z.pk, "warehouse_id": self.second_warehouse.pk,
            "quantity": 1, "sale_price": "100.000",
        }])
        self.fulfill_onsite(target_order)
        self.assert_replay()

        sku_y.is_active = False
        sku_y.save(update_fields=("is_active",))
        with self.assertRaises(OperationError) as raised:
            preview_cost_correction(self.user, {
                "receipt_id": source.pk, "reason": "Invalid inactive line remap",
                "lines": [{"line_number": 1, "sku_id": sku_y.pk}],
            })
        self.assertEqual(raised.exception.code, "inactive_reference")
        sku_y.is_active = True
        sku_y.save(update_fields=("is_active",))

        preview = preview_cost_correction(self.user, self.correction_data(
            source, warehouse=self.second_warehouse, purchase_price="31.000",
        ))
        self.assertTrue(preview["approval_required"])
        changed_balances = {
            (row["warehouse_id"], row["sku_id"]) for row in preview["changes"]["balances"]
        }
        self.assertTrue({(self.warehouse.pk, self.sku.pk), (self.warehouse.pk, sku_y.pk),
                         (self.second_warehouse.pk, self.sku.pk), (self.second_warehouse.pk, sku_y.pk)} <= changed_balances)
        result = submit_cost_correction(self.user, uuid4(), preview["preview_token"])
        self.approve_correction(CostCorrection.objects.get(pk=result["cost_correction_id"]))
        source.refresh_from_db()
        self.assertEqual(source.warehouse_id, self.second_warehouse.pk)
        self.assert_replay()

    def test_cost_preview_uses_one_repeatable_read_snapshot_and_expires_after_new_purchase(self):
        receipt = self.receive(quantity_value=10, price="10.000")
        expected_sequence = BusinessControl.objects.get(pk=1).last_sequence
        sku = self.sku
        payload = self.receipt_payload(sku, quantity_value=10, price="20.000")
        original_source = correction_service._source

        def source_then_commit(*args, **kwargs):
            source_data = original_source(*args, **kwargs)

            def purchase():
                close_old_connections()
                try:
                    return self.receive(sku, quantity_value=10, price="20.000")
                finally:
                    close_old_connections()

            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(purchase).result(timeout=20)
            return source_data

        with patch("crm.corrections._source", side_effect=source_then_commit):
            preview = preview_cost_correction(self.user, self.correction_data(receipt, purchase_price="11.000"))
        self.assertEqual(preview["sequence"], expected_sequence)
        self.assertEqual(BusinessControl.objects.get(pk=1).last_sequence, expected_sequence + 1)
        balance_change = next(row for row in preview["changes"]["balances"] if row["sku_id"] == self.sku.pk)
        self.assertEqual(balance_change["after"]["on_hand"], 10)
        with self.assertRaises(OperationError) as raised:
            submit_cost_correction(self.user, uuid4(), preview["preview_token"])
        self.assertEqual(raised.exception.code, "stale_preview")
        self.assert_replay()

    def test_postgresql_competing_gifts_cannot_overdraw_and_correction_race_expires_one_preview(self):
        self.assertEqual(connection.vendor, "postgresql")
        receipt = self.receive(quantity_value=10, price="10.000")
        barrier = Barrier(2)

        def gift_worker():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                barrier.wait(timeout=10)
                try:
                    result = create_inventory_gift(self.user, {"reason": "Competing stock draw", "lines": [{
                        "warehouse_id": self.warehouse.pk, "sku_id": self.sku.pk, "quantity": 8,
                    }]}, uuid4())
                    return pid, "ok", result
                except OperationError as exc:
                    return pid, exc.code, None
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            gift_outcomes = [future.result(timeout=20) for future in (pool.submit(gift_worker), pool.submit(gift_worker))]
        self.assertEqual(len({pid for pid, _, _ in gift_outcomes}), 2)
        self.assertCountEqual([status for _, status, _ in gift_outcomes], ["ok", "stock_conflict"])
        balance = self.balance_for()
        self.assertEqual((balance.on_hand, GiftRecord.objects.count()), (2, 1))
        self.assert_replay()

        # Establish a pending, impactful correction and race its approval with
        # a receipt confirmation that carries the same global sequence.
        order = self.new_order(lines=[{
            "sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk,
            "quantity": 1, "sale_price": "20.000",
        }])
        correction_preview = preview_cost_correction(self.user, self.correction_data(
            receipt, purchase_price="11.000",
        ))
        self.assertTrue(correction_preview["approval_required"])
        correction_result = submit_cost_correction(self.user, uuid4(), correction_preview["preview_token"])
        correction = CostCorrection.objects.get(pk=correction_result["cost_correction_id"])
        receipt_payload = self.receipt_payload(self.sku, quantity_value=1, price="12.000")
        receipt_preview = preview_receipt(self.user, receipt_payload)
        receipt_key = uuid4()
        barrier = Barrier(2)

        def correction_worker():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                barrier.wait(timeout=10)
                try:
                    result = approve_cost_correction(self.owner, correction.pk, {
                        "operation_key": str(uuid4()), "expected_version": correction.version,
                    })
                    return pid, "ok", result
                except OperationError as exc:
                    return pid, exc.code, None
            finally:
                close_old_connections()

        def receipt_worker():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                barrier.wait(timeout=10)
                try:
                    result = confirm_receipt(self.user, receipt_key, receipt_preview["preview_token"])
                    return pid, "ok", result
                except OperationError as exc:
                    return pid, exc.code, None
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = [future.result(timeout=20) for future in (
                pool.submit(correction_worker), pool.submit(receipt_worker),
            )]
        self.assertEqual(len({pid for pid, _, _ in outcomes}), 2)
        statuses = {"correction": outcomes[0][1], "receipt": outcomes[1][1]}
        self.assertCountEqual(list(statuses.values()), ["ok", "stale_preview"])
        if statuses["correction"] == "stale_preview":
            correction.refresh_from_db()
            reject_cost_correction(self.owner, correction.pk, {
                "operation_key": str(uuid4()), "expected_version": correction.version,
            })
            refreshed = preview_cost_correction(self.user, self.correction_data(receipt, purchase_price="11.000"))
            resubmitted = submit_cost_correction(self.user, uuid4(), refreshed["preview_token"])
            self.approve_correction(CostCorrection.objects.get(pk=resubmitted["cost_correction_id"]))
        if statuses["receipt"] == "stale_preview":
            fresh = preview_receipt(self.user, receipt_payload)
            confirm_receipt(self.user, uuid4(), fresh["preview_token"])
        self.assert_replay()

    def test_cost_correction_failure_rolls_back_source_inventory_and_approval(self):
        receipt = self.receive(quantity_value=10, price="10.000")
        order = self.new_order(lines=[{
            "sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk,
            "quantity": 5, "sale_price": "20.000",
        }])
        preview = preview_cost_correction(self.user, self.correction_data(receipt, purchase_price="20.000"))
        submitted = submit_cost_correction(self.user, uuid4(), preview["preview_token"])
        correction = CostCorrection.objects.get(pk=submitted["cost_correction_id"])
        self.user.user_permissions.add(Permission.objects.get(codename="approve_costcorrection"))
        self.user = type(self.user).objects.get(pk=self.user.pk)
        with self.assertRaises(OperationError) as raised:
            approve_cost_correction(self.user, correction.pk, {
                "operation_key": str(uuid4()), "expected_version": correction.version,
            })
        self.assertEqual(raised.exception.code, "self_approval_denied")
        before = (
            ReceiptLine.objects.get(receipt=receipt).purchase_price,
            self.balance_for().on_hand,
            self.balance_for().cost_amount,
            SalesOrderLine.objects.get(order=order, active=True).unit_cost_snapshot,
            BusinessControl.objects.get(pk=1).last_sequence,
        )
        original_save = InventoryMovement.save

        def fail_movement_save(instance, *args, **kwargs):
            raise RuntimeError("synthetic correction movement failure")

        with patch.object(InventoryMovement, "save", new=fail_movement_save):
            with self.assertRaises(RuntimeError):
                self.approve_correction(correction)
        correction.refresh_from_db()
        after = (
            ReceiptLine.objects.get(receipt=receipt).purchase_price,
            self.balance_for().on_hand,
            self.balance_for().cost_amount,
            SalesOrderLine.objects.get(order=order, active=True).unit_cost_snapshot,
            BusinessControl.objects.get(pk=1).last_sequence,
        )
        self.assertEqual(after, before)
        self.assertEqual(correction.status, CostCorrection.Status.PENDING)
        self.assertFalse(BusinessOperation.objects.filter(action="cost_correction_approve").exists())
        self.assert_replay()

    def test_count_cannot_make_current_occupied_stock_illegal(self):
        self.receive(quantity_value=10, price="1.000")
        order = self.new_order(lines=[{
            "sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk,
            "quantity": 8, "sale_price": "5.000",
        }])
        count_result = create_stock_count(self.user, {"reason": "Observed only seven", "lines": [{
            "warehouse_id": self.warehouse.pk, "sku_id": self.sku.pk, "observed_quantity": 7,
        }]}, uuid4())
        count = StockCount.objects.get(pk=count_result["stock_count_id"])
        versions = {str(line.pk): line.balance.version for line in count.lines.select_related("balance")}
        before_sequence = BusinessControl.objects.get(pk=1).last_sequence
        with self.assertRaises(OperationError):
            approve_stock_count(self.owner, count.pk, {
                "operation_key": str(uuid4()), "expected_version": count.version,
                "balance_versions": versions,
            })
        balance = self.balance_for()
        self.assertEqual((balance.on_hand, balance.occupied), (10, 8))
        self.assertEqual(StockCount.objects.get(pk=count.pk).status, StockCount.Status.PENDING)
        self.assertEqual(BusinessControl.objects.get(pk=1).last_sequence, before_sequence)
        self.assert_replay()
