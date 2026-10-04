from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from io import BytesIO
import json
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection
from django.test import Client, override_settings
from PIL import Image

from .fulfillment import (
    deliver_on_site,
    receive_sales_order,
    register_payment,
    review_commission,
    ship_sales_order,
    update_outbound_freight,
    verify_payment,
)
from .models import (
    CommissionAdjustment,
    Customer,
    InventoryBalance,
    InventoryMovement,
    PaymentAccount,
    PaymentRecord,
    ReceiptImage,
    SalesOrder,
    SalesOrderLine,
    Shipment,
)
from .operations import OperationError, confirm_receipt, preview_receipt
from .permissions import ROLE_OWNER, ROLE_SALES, ROLE_WAREHOUSE
from .sales import confirm_sales_order, create_customer, preview_sales_order
from .test_phase3 import Phase3Base


User = get_user_model()


class Phase4Base(Phase3Base):
    def setUp(self):
        super().setUp()
        self.public_account = PaymentAccount.objects.create(
            name="Synthetic Public Bank",
            method=PaymentAccount.Method.BANK,
            responsibility=PaymentAccount.Responsibility.PUBLIC,
        )
        self.private_account = PaymentAccount.objects.create(
            name="Synthetic Owner Wallet",
            method=PaymentAccount.Method.WECHAT,
            responsibility=PaymentAccount.Responsibility.OWNER_PRIVATE,
        )

    def new_order(self, *, delivery_mode=SalesOrder.DeliveryMode.ON_SITE, lines=None, sale_price="5.000"):
        address_snapshot = {}
        if delivery_mode == SalesOrder.DeliveryMode.SHIPMENT:
            address_snapshot = {
                "recipient": "Synthetic Recipient",
                "contact": "Synthetic Contact",
                "province": "北京市",
                "city": "北京市",
                "detail": "Synthetic street 1",
            }
        payload = self.order_payload(
            delivery_mode=delivery_mode,
            lines=lines or [{"sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk, "quantity": 2, "sale_price": sale_price}],
            address_snapshot=address_snapshot,
        )
        _, _, order = self.preview_and_confirm(payload)
        return order

    def add_payment_image(self, owner=None):
        return ReceiptImage.objects.create(
            owner=owner or self.sales,
            file="receipt-images/synthetic.image",
            image_format="PNG",
            size=68,
        )

    def register(self, order, *, account=None, received_amount=None, difference_reason="", key=None, image=None):
        order.refresh_from_db()
        image = image or self.add_payment_image()
        return register_payment(self.sales, order.pk, {
            "operation_key": str(key or uuid4()),
            "expected_version": order.version,
            "account_id": (account or self.public_account).pk,
            "received_amount": str(order.revenue_amount if received_amount is None else received_amount),
            "image_ids": [image.pk],
            "difference_reason": difference_reason,
        })

    def verify(self, order, actor=None, *, result="passed", problem_reason="", key=None):
        order.refresh_from_db()
        return verify_payment(actor or self.user, order.pk, {
            "operation_key": str(key or uuid4()),
            "expected_version": order.version,
            "result": result,
            "problem_reason": problem_reason,
        })

    def shipment_data(self, order, *, key=None, tracking_number="SYNTHETIC-WAYBILL", freight_rows=None):
        warehouse_ids = sorted(set(order.lines.filter(active=True).values_list("warehouse_id", flat=True)))
        return {
            "operation_key": str(key or uuid4()),
            "expected_version": order.version,
            "shipments": [{
                "carrier": "sf",
                "tracking_number": tracking_number,
                "warehouse_ids": warehouse_ids,
            }],
            "outbound_freights": freight_rows or [],
        }

    def assert_operation_error(self, code, callback):
        with self.assertRaises(OperationError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)


class CustomerCreationPermissionPhase4Tests(Phase4Base):
    def test_warehouse_can_create_for_active_sales_without_customer_read_scope(self):
        self.assertTrue(self.user.has_perm("crm.add_customer"))
        self.assertFalse(self.user.has_perm("crm.view_customer"))
        self.assertFalse(self.user.has_perm("crm.view_all_customers"))
        customer = create_customer(self.user, self.customer_payload(owner_id=self.sales.pk), uuid4())
        self.assertEqual(Customer.objects.get(pk=customer["customer_id"]).owner_id, self.sales.pk)

        client = Client()
        client.force_login(self.user)
        data = self.customer_payload(owner_id=self.sales_two.pk)
        data["operation_key"] = str(uuid4())
        response = client.post("/api/v1/customers", data=json.dumps(data), content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Customer.objects.get(pk=response.json()["customer_id"]).owner_id, self.sales_two.pk)
        self.assertEqual(client.get("/api/v1/customers").status_code, 403)

        for owner_id in (None, self.user.pk):
            with self.subTest(owner_id=owner_id):
                data = self.customer_payload()
                if owner_id is not None:
                    data["owner_id"] = owner_id
                with self.assertRaises(OperationError):
                    create_customer(self.user, data, uuid4())

        inactive_sales = User.objects.create_user(username="inactive-sales", is_active=False)
        inactive_sales.groups.add(Group.objects.get(name=ROLE_SALES))
        with self.assertRaises(OperationError):
            create_customer(self.user, self.customer_payload(owner_id=inactive_sales.pk), uuid4())


class PaymentPhase4Tests(Phase4Base):
    def test_payment_requires_images_and_reason_for_difference_then_is_idempotent(self):
        order = self.new_order()
        with self.assertRaises(OperationError):
            register_payment(self.sales, order.pk, {
                "operation_key": str(uuid4()), "expected_version": order.version,
                "account_id": self.public_account.pk, "received_amount": "10.000", "image_ids": [],
            })
        image = self.add_payment_image()
        base = {
            "operation_key": str(uuid4()),
            "expected_version": order.version,
            "account_id": self.public_account.pk,
            "received_amount": "9.000",
            "image_ids": [image.pk],
        }
        with self.assertRaises(OperationError) as raised:
            register_payment(self.sales, order.pk, base)
        self.assertIn("difference_reason", raised.exception.fields)
        self.assertEqual(order.status, SalesOrder.Status.AWAITING_PAYMENT)

        base["difference_reason"] = "Synthetic bank fee difference"
        result = register_payment(self.sales, order.pk, base)
        replay = register_payment(self.sales, order.pk, base)
        self.assertEqual(replay, result)
        order.refresh_from_db()
        payment = PaymentRecord.objects.get(order=order)
        self.assertEqual(order.status, SalesOrder.Status.READY_TO_SHIP)
        self.assertEqual(payment.status, PaymentRecord.Status.PENDING)
        self.assertEqual(payment.received_amount, Decimal("9.000"))
        self.assertEqual(payment.expected_amount, Decimal("10.000"))
        self.assertEqual(payment.images.count(), 1)

        conflicting = dict(base, received_amount="8.000")
        self.assert_operation_error("idempotency_conflict", lambda: register_payment(self.sales, order.pk, conflicting))

    def test_account_responsibility_and_pause_survive_re_registration(self):
        order = self.new_order()
        self.register(order, account=self.private_account)
        self.assert_operation_error("permission_denied", lambda: self.verify(order, actor=self.user))
        self.verify(order, actor=self.owner, result="problem", problem_reason="Synthetic amount mismatch")
        order.refresh_from_db()
        payment = PaymentRecord.objects.get(order=order)
        self.assertTrue(payment.paused)
        self.assertEqual(order.status, SalesOrder.Status.READY_TO_SHIP)

        self.register(order, account=self.private_account, received_amount="10.000", difference_reason="corrected receipt")
        payment.refresh_from_db()
        self.assertTrue(payment.paused)
        self.assertEqual(payment.status, PaymentRecord.Status.PENDING)
        self.assertEqual(payment.problem_reason, "")
        self.verify(order, actor=self.owner)
        payment.refresh_from_db()
        self.assertFalse(payment.paused)
        self.assertEqual(payment.status, PaymentRecord.Status.PASSED)

    def test_private_payment_account_is_listed_but_only_owner_verifies(self):
        client = Client()
        client.force_login(self.sales)
        response = client.get("/api/v1/payment-accounts")
        self.assertEqual(response.status_code, 200)
        self.assertEqual({item["id"] for item in response.json()["items"]}, {self.public_account.pk, self.private_account.pk})
        order = self.new_order()
        self.register(order, account=self.private_account)
        with self.assertRaises(OperationError):
            self.verify(order, actor=self.user, result="passed")

    def test_payment_upload_validates_content_and_payment_image_access_is_private(self):
        import tempfile

        client = Client()
        client.force_login(self.sales)
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            image_ids = []
            for image_format, mime in (("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")):
                buffer = BytesIO()
                Image.new("RGB", (2, 2), "white").save(buffer, format=image_format)
                response = client.post("/api/v1/images", {"file": SimpleUploadedFile(f"receipt.{image_format.lower()}", buffer.getvalue(), content_type=mime)})
                self.assertEqual(response.status_code, 200, response.content)
                image_ids.append(int(response.json()["image_id"]))

            buffer = BytesIO()
            Image.new("RGB", (2, 2), "white").save(buffer, format="PNG")
            bad_mime = client.post("/api/v1/images", {"file": SimpleUploadedFile("wrong.jpg", buffer.getvalue(), content_type="image/jpeg")})
            self.assertEqual(bad_mime.status_code, 400)
            corrupt = client.post("/api/v1/images", {"file": SimpleUploadedFile("broken.png", b"not an image", content_type="image/png")})
            self.assertEqual(corrupt.status_code, 400)
            oversized = client.post("/api/v1/images", {"file": SimpleUploadedFile("large.png", b"x" * (10 * 1024 * 1024 + 1), content_type="image/png")})
            self.assertEqual(oversized.status_code, 400)

            order = self.new_order()
            order.refresh_from_db()
            response = register_payment(self.sales, order.pk, {
                "operation_key": str(uuid4()),
                "expected_version": order.version,
                "account_id": self.public_account.pk,
                "received_amount": "10.000",
                "image_ids": image_ids,
            })
            bound_image = ReceiptImage.objects.get(pk=image_ids[0])
            self.assertIsNotNone(bound_image.payment_id)
            self.assertEqual(client.get(f"/api/v1/images/{bound_image.pk}").status_code, 200)

            other = Client()
            other.force_login(self.sales_two)
            self.assertEqual(other.get(f"/api/v1/images/{bound_image.pk}").status_code, 404)
            warehouse = Client()
            warehouse.force_login(self.user)
            self.assertEqual(warehouse.get(f"/api/v1/images/{bound_image.pk}").status_code, 200)

    def test_payment_images_limit_twenty_and_orphan_cleanup_keeps_bound_images(self):
        import tempfile
        from datetime import timedelta
        from django.core.management import call_command
        from django.utils import timezone

        order = self.new_order()
        images = [self.add_payment_image() for _ in range(20)]
        order.refresh_from_db()
        register_payment(self.sales, order.pk, {
            "operation_key": str(uuid4()), "expected_version": order.version, "account_id": self.public_account.pk,
            "received_amount": "10.000", "image_ids": [image.pk for image in images],
        })
        extra = self.add_payment_image()
        order.refresh_from_db()
        with self.assertRaises(OperationError):
            register_payment(self.sales, order.pk, {
                "operation_key": str(uuid4()), "expected_version": order.version, "account_id": self.public_account.pk,
                "received_amount": "10.000", "image_ids": [extra.pk],
            })

        orphan = self.add_payment_image()
        orphan.created_at = timezone.now() - timedelta(hours=25)
        orphan.save(update_fields=("created_at",))
        images[0].refresh_from_db()
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            call_command("cleanup_orphan_receipt_images")
        self.assertTrue(ReceiptImage.objects.filter(pk=images[0].pk).exists())
        self.assertFalse(ReceiptImage.objects.filter(pk=orphan.pk).exists())


class FulfillmentPhase4Tests(Phase4Base):
    def test_on_site_delivery_deducts_stock_once_and_has_no_shipment(self):
        order = self.new_order()
        self.register(order)
        self.verify(order)
        order.refresh_from_db()
        before = InventoryBalance.objects.get(pk=self.balance.pk)
        replay_data = {"operation_key": str(uuid4()), "expected_version": order.version}
        result = deliver_on_site(self.user, order.pk, replay_data)
        self.assertEqual(deliver_on_site(self.user, order.pk, replay_data), result)
        order.refresh_from_db()
        balance = InventoryBalance.objects.get(pk=self.balance.pk)
        self.assertEqual(order.status, SalesOrder.Status.COMPLETED)
        self.assertEqual(Shipment.objects.filter(order=order).count(), 0)
        self.assertEqual(balance.on_hand, before.on_hand - 2)
        self.assertEqual(balance.occupied, 0)
        self.assertEqual(InventoryMovement.objects.filter(sales_order=order, event_kind="order_outbound").count(), 1)
        self.assert_operation_error("state_conflict", lambda: deliver_on_site(self.user, order.pk, {"operation_key": str(uuid4()), "expected_version": order.version}))

    def test_pending_payment_allows_multiwarehouse_ship_and_sales_receive_without_second_deduction(self):
        second_sku = self.new_sku(2)
        second_balance = InventoryBalance.objects.create(sku=second_sku, warehouse=self.second_warehouse, on_hand=3, cost_amount=Decimal("6.000"))
        lines = [
            {"sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk, "quantity": 1, "sale_price": "5.000"},
            {"sku_id": second_sku.pk, "warehouse_id": self.second_warehouse.pk, "quantity": 1, "sale_price": "9.000"},
        ]
        order = self.new_order(delivery_mode=SalesOrder.DeliveryMode.SHIPMENT, lines=lines)
        self.register(order)
        self.assertEqual(PaymentRecord.objects.get(order=order).status, PaymentRecord.Status.PENDING)
        order.refresh_from_db()
        data = {
            "operation_key": str(uuid4()),
            "expected_version": order.version,
            "shipments": [
                {"carrier": "sf", "tracking_number": "SF-SYNTHETIC", "warehouse_ids": [self.warehouse.pk]},
                {"carrier": "huolala", "tracking_number": "HH-SYNTHETIC", "warehouse_ids": [self.second_warehouse.pk]},
            ],
        }
        result = ship_sales_order(self.user, order.pk, data)
        self.assertEqual(ship_sales_order(self.user, order.pk, data), result)
        changed_data = dict(data)
        changed_data["shipments"] = [dict(data["shipments"][0], tracking_number="SF-OTHER") , data["shipments"][1]]
        self.assert_operation_error("idempotency_conflict", lambda: ship_sales_order(self.user, order.pk, changed_data))
        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.Status.SHIPPED)
        self.assertEqual(order.shipments.count(), 2)
        self.assertEqual(InventoryBalance.objects.get(pk=self.balance.pk).on_hand, 2)
        self.assertEqual(InventoryBalance.objects.get(pk=second_balance.pk).on_hand, 2)
        before_movements = InventoryMovement.objects.filter(sales_order=order, event_kind="order_outbound").count()
        self.assertEqual(before_movements, 2)

        receive_data = {"operation_key": str(uuid4()), "expected_version": order.version}
        receive_result = receive_sales_order(self.sales, order.pk, receive_data)
        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.Status.COMPLETED)
        self.assertEqual(InventoryMovement.objects.filter(sales_order=order, event_kind="order_outbound").count(), before_movements)
        self.assertEqual(receive_sales_order(self.sales, order.pk, receive_data), receive_result)

    def test_shipping_requires_all_existing_warehouses_and_rejects_new_warehouse(self):
        second_sku = self.new_sku(2)
        InventoryBalance.objects.create(sku=second_sku, warehouse=self.second_warehouse, on_hand=2, cost_amount=Decimal("4.000"))
        order = self.new_order(delivery_mode=SalesOrder.DeliveryMode.SHIPMENT, lines=[
            {"sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk, "quantity": 1, "sale_price": "5.000"},
            {"sku_id": second_sku.pk, "warehouse_id": self.second_warehouse.pk, "quantity": 1, "sale_price": "9.000"},
        ])
        self.register(order)
        order.refresh_from_db()
        missing = {
            "operation_key": str(uuid4()), "expected_version": order.version,
            "shipments": [{"carrier": "crossing", "tracking_number": "CX-SYNTHETIC", "warehouse_ids": [self.warehouse.pk]}],
        }
        self.assert_operation_error("warehouse_coverage", lambda: ship_sales_order(self.user, order.pk, missing))
        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.Status.READY_TO_SHIP)
        self.assertEqual(InventoryBalance.objects.get(pk=self.balance.pk).on_hand, 3)

    def test_actual_cost_uses_current_weighted_average_and_empty_cost_clears(self):
        self.balance.on_hand = 10
        self.balance.cost_amount = Decimal("1000.000")
        self.balance.save(update_fields=("on_hand", "cost_amount"))
        order = self.new_order(delivery_mode=SalesOrder.DeliveryMode.SHIPMENT, lines=[{
            "sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk, "quantity": 5, "sale_price": "200.000",
        }])
        self.assertEqual(order.cost_amount, Decimal("500.000"))
        self.register(order)
        inbound = self.receipt_payload(self.sku, quantity_value=10, price="200.000")
        preview = preview_receipt(self.user, inbound)
        confirm_receipt(self.user, uuid4(), preview["preview_token"])
        order.refresh_from_db()
        shipment = self.shipment_data(order)
        ship_sales_order(self.user, order.pk, shipment)
        balance = InventoryBalance.objects.get(pk=self.balance.pk)
        self.assertEqual(balance.on_hand, 15)
        self.assertEqual(balance.occupied, 0)
        self.assertEqual(balance.cost_amount, Decimal("2250.000"))

        final_order = self.new_order(delivery_mode=SalesOrder.DeliveryMode.ON_SITE, lines=[{
            "sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk, "quantity": 15, "sale_price": "300.000",
        }])
        self.register(final_order)
        final_order.refresh_from_db()
        deliver_on_site(self.user, final_order.pk, {"operation_key": str(uuid4()), "expected_version": final_order.version})
        balance.refresh_from_db()
        self.assertEqual(balance.on_hand, 0)
        self.assertEqual(balance.cost_amount, Decimal("0.000"))

    def test_freight_updates_margin_versions_and_commission_re_review_delta(self):
        order = self.new_order(sale_price="0.100")
        line = order.lines.get(active=True)
        self.assertEqual(order.margin_amount, Decimal("-0.466"))
        self.assertEqual(order.commission_estimate, Decimal("-0.047"))
        self.register(order, received_amount="0.200")
        self.verify(order)
        order.refresh_from_db()
        result = deliver_on_site(self.user, order.pk, {
            "operation_key": str(uuid4()),
            "expected_version": order.version,
            "outbound_freights": [{"line_id": line.pk, "amount": "0.100"}],
        })
        order.refresh_from_db()
        line.refresh_from_db()
        self.assertEqual(line.outbound_freight, Decimal("0.100"))
        self.assertEqual(order.margin_amount, Decimal("-0.566"))
        self.assertEqual(order.commission_estimate, Decimal("-0.057"))
        self.assertEqual(order.amount_version, 2)

        first = review_commission(self.owner, order.pk, {"operation_key": str(uuid4()), "expected_version": order.version})
        review = order.commission_review.__class__.objects.get(order=order)
        earned_at = review.first_earned_at
        self.assertEqual(review.approved_amount, Decimal("-0.057"))
        self.assertEqual(first["delta"], "-0.057")

        order.refresh_from_db()
        update_outbound_freight(self.user, order.pk, {
            "operation_key": str(uuid4()),
            "expected_version": order.version,
            "outbound_freights": [{"line_id": line.pk, "amount": "0.050"}],
        })
        order.refresh_from_db()
        self.assertEqual(order.commission_estimate, Decimal("-0.052"))
        self.assertEqual(order.commission_review.approved_amount, Decimal("-0.057"))
        second = review_commission(self.owner, order.pk, {"operation_key": str(uuid4()), "expected_version": order.version})
        review.refresh_from_db()
        self.assertEqual(second["delta"], "0.005")
        self.assertEqual(review.approved_amount, Decimal("-0.052"))
        self.assertEqual(review.first_earned_at, earned_at)

        first_adjustment = CommissionAdjustment.objects.get(pk=first["adjustment_id"])
        self.assert_operation_error("not_found", lambda: self.complete_adjustment_as(self.sales_two, first_adjustment.pk))
        self.complete_adjustment_as(self.sales, first_adjustment.pk)
        first_adjustment.refresh_from_db()
        self.assertEqual(first_adjustment.completed_by_id, self.sales.pk)

        order.refresh_from_db()
        update_outbound_freight(self.user, order.pk, {
            "operation_key": str(uuid4()),
            "expected_version": order.version,
            "outbound_freights": [{"line_id": line.pk, "amount": "0"}],
        })
        line.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(line.outbound_freight, Decimal("0.000"))
        self.assertEqual(order.margin_amount, Decimal("-0.466"))
        self.assertEqual(order.commission_estimate, Decimal("-0.047"))

    def complete_adjustment_as(self, actor, adjustment_id):
        from .fulfillment import complete_commission_adjustment

        adjustment = CommissionAdjustment.objects.get(pk=adjustment_id)
        order = adjustment.order
        return complete_commission_adjustment(actor, adjustment_id, {
            "operation_key": str(uuid4()), "expected_version": order.version,
        })

    def test_delivered_payment_problem_does_not_roll_back_fulfillment(self):
        order = self.new_order(delivery_mode=SalesOrder.DeliveryMode.SHIPMENT)
        self.register(order)
        order.refresh_from_db()
        ship_sales_order(self.user, order.pk, self.shipment_data(order))
        order.refresh_from_db()
        self.verify(order, actor=self.user, result="problem", problem_reason="Synthetic late mismatch")
        order.refresh_from_db()
        payment = PaymentRecord.objects.get(order=order)
        self.assertEqual(order.status, SalesOrder.Status.SHIPPED)
        self.assertFalse(payment.paused)
        before = InventoryMovement.objects.filter(sales_order=order, event_kind="order_outbound").count()
        receive_sales_order(self.sales, order.pk, {"operation_key": str(uuid4()), "expected_version": order.version})
        self.assertEqual(InventoryMovement.objects.filter(sales_order=order, event_kind="order_outbound").count(), before)

    def test_cancel_paid_undelivered_order_releases_holds_and_keeps_payment(self):
        order = self.new_order()
        self.register(order)
        order.refresh_from_db()
        before_occupied = InventoryBalance.objects.get(pk=self.balance.pk).occupied
        from .sales import cancel_sales_order

        cancel_sales_order(self.sales, order.pk, {"expected_version": order.version, "operation_key": str(uuid4())})
        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.Status.CANCELLED)
        self.assertEqual(InventoryBalance.objects.get(pk=self.balance.pk).occupied, before_occupied - 2)
        self.assertTrue(PaymentRecord.objects.filter(order=order, images__isnull=False).exists())

    def test_ready_order_edit_preserves_pause_and_stale_version_blocks_shipment(self):
        order = self.new_order()
        self.register(order)
        self.verify(order, result="problem", problem_reason="Synthetic review pause")
        order.refresh_from_db()
        initial_occupied = InventoryBalance.objects.get(pk=self.balance.pk).occupied
        stale_version = order.version
        payload = self.amendment_payload(order)
        payload["lines"][0]["quantity"] = 1
        preview = preview_sales_order(self.sales, payload, order_id=order.pk)
        result = confirm_sales_order(self.sales, uuid4(), preview["preview_token"])
        order.refresh_from_db()
        payment = PaymentRecord.objects.get(order=order)
        self.assertEqual(result["status"], SalesOrder.Status.READY_TO_SHIP)
        self.assertEqual(InventoryBalance.objects.get(pk=self.balance.pk).occupied, initial_occupied - 1)
        self.assertTrue(payment.paused)
        self.assertEqual(payment.status, PaymentRecord.Status.PENDING)
        self.assertLess(payment.expected_payment_version, order.payment_version)
        self.assert_operation_error("version_conflict", lambda: verify_payment(self.user, order.pk, {
            "operation_key": str(uuid4()), "expected_version": stale_version,
            "result": "passed",
        }))
        self.assert_operation_error("stale_payment", lambda: deliver_on_site(self.user, order.pk, {
            "operation_key": str(uuid4()), "expected_version": order.version,
        }))

    def test_order_amount_rounding_matches_t03_two_half_mils(self):
        second_sku = self.new_sku(2)
        InventoryBalance.objects.create(sku=second_sku, warehouse=self.second_warehouse, on_hand=1, cost_amount=Decimal("0.333"))
        order = self.new_order(lines=[
            {"sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk, "quantity": 1, "sale_price": "0.338"},
            {"sku_id": second_sku.pk, "warehouse_id": self.second_warehouse.pk, "quantity": 1, "sale_price": "0.338"},
        ])
        self.assertEqual(order.revenue_amount, Decimal("0.676"))
        self.assertEqual(order.cost_amount, Decimal("0.666"))
        self.assertEqual(order.margin_amount, Decimal("0.010"))
        self.assertEqual(order.commission_estimate, Decimal("0.001"))

    def test_revoked_verification_permission_takes_effect_before_write(self):
        order = self.new_order()
        self.register(order)
        group = Group.objects.get(name=ROLE_WAREHOUSE)
        group.permissions.remove(Permission.objects.get(content_type__app_label="crm", codename="verify_payment"))
        order.refresh_from_db()
        version = order.version
        self.assert_operation_error("permission_denied", lambda: verify_payment(self.user, order.pk, {
            "operation_key": str(uuid4()), "expected_version": version, "result": "passed",
        }))
        order.refresh_from_db()
        self.assertEqual(order.version, version)
        self.assertEqual(PaymentRecord.objects.get(order=order).status, PaymentRecord.Status.PENDING)

    def test_order_detail_shows_role_specific_stage4_actions(self):
        order = self.new_order(delivery_mode=SalesOrder.DeliveryMode.SHIPMENT)
        self.register(order)
        sales_client = Client()
        sales_client.force_login(self.sales)
        sales_response = sales_client.get(f"/orders/{order.pk}/")
        self.assertContains(sales_response, "登记或更新收款")
        self.assertNotContains(sales_response, "确认整单发货并扣减库存")

        warehouse_client = Client()
        warehouse_client.force_login(self.user)
        warehouse_response = warehouse_client.get(f"/orders/{order.pk}/")
        self.assertContains(warehouse_response, "收款核对")
        self.assertContains(warehouse_response, "确认整单发货并扣减库存")

    def test_second_balance_failure_rolls_back_ship_and_same_key_can_retry(self):
        second_sku = self.new_sku(2)
        second_balance = InventoryBalance.objects.create(sku=second_sku, warehouse=self.second_warehouse, on_hand=2, cost_amount=Decimal("4.000"))
        order = self.new_order(delivery_mode=SalesOrder.DeliveryMode.SHIPMENT, lines=[
            {"sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk, "quantity": 1, "sale_price": "5.000"},
            {"sku_id": second_sku.pk, "warehouse_id": self.second_warehouse.pk, "quantity": 1, "sale_price": "9.000"},
        ])
        self.register(order)
        order.refresh_from_db()
        line_ids = list(order.lines.filter(active=True).values_list("pk", flat=True))
        data = {
            "operation_key": str(uuid4()),
            "expected_version": order.version,
            "shipments": [
                {"carrier": "crossing", "tracking_number": "CX-ROLLBACK-1", "warehouse_ids": [self.warehouse.pk]},
                {"carrier": "sf", "tracking_number": "SF-ROLLBACK-2", "warehouse_ids": [self.second_warehouse.pk]},
            ],
            "outbound_freights": [{"line_id": line_id, "amount": "1.000"} for line_id in line_ids],
        }
        manager = InventoryMovement.objects
        real_create = manager.create
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("synthetic second outbound failure")
            return real_create(*args, **kwargs)

        with patch.object(manager, "create", side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, "second outbound"):
                ship_sales_order(self.user, order.pk, data)
        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.Status.READY_TO_SHIP)
        self.assertEqual(InventoryBalance.objects.get(pk=self.balance.pk).on_hand, 3)
        self.assertEqual(InventoryBalance.objects.get(pk=second_balance.pk).on_hand, 2)
        self.assertEqual(order.shipments.count(), 0)
        self.assertEqual(list(order.lines.filter(active=True).values_list("outbound_freight", flat=True)), [Decimal("0.000"), Decimal("0.000")])
        self.assertEqual(order.amount_version, 1)
        self.assertFalse(InventoryMovement.objects.filter(sales_order=order, event_kind="order_outbound").exists())
        result = ship_sales_order(self.user, order.pk, data)
        self.assertEqual(result["status"], SalesOrder.Status.SHIPPED)
        order.refresh_from_db()
        self.assertEqual(order.amount_version, 2)
        self.assertEqual(sum(order.lines.filter(active=True).values_list("outbound_freight", flat=True)), Decimal("2.000"))

    def test_commission_review_requires_completed_order_and_current_payment_pass(self):
        order = self.new_order()
        self.register(order)
        order.refresh_from_db()
        self.assert_operation_error("state_conflict", lambda: review_commission(self.owner, order.pk, {
            "operation_key": str(uuid4()), "expected_version": order.version,
        }))
        self.verify(order)
        order.refresh_from_db()
        deliver_on_site(self.user, order.pk, {"operation_key": str(uuid4()), "expected_version": order.version})
        order.refresh_from_db()
        self.assertEqual(review_commission(self.owner, order.pk, {
            "operation_key": str(uuid4()), "expected_version": order.version,
        })["status"], SalesOrder.Status.COMPLETED)

    def test_postgresql_problem_and_ship_race_never_leaves_partial_state(self):
        self.assertEqual(connection.vendor, "postgresql")
        order = self.new_order(delivery_mode=SalesOrder.DeliveryMode.SHIPMENT)
        self.register(order)
        order.refresh_from_db()
        version = order.version
        barrier = Barrier(2)

        def problem_worker():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                barrier.wait(timeout=10)
                try:
                    result = verify_payment(self.user, order.pk, {
                        "operation_key": str(uuid4()), "expected_version": version,
                        "result": "problem", "problem_reason": "Synthetic concurrency mismatch",
                    })
                    return pid, result["status"]
                except OperationError as exc:
                    return pid, exc.code
            finally:
                close_old_connections()

        def ship_worker():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pid = cursor.fetchone()[0]
                barrier.wait(timeout=10)
                try:
                    result = ship_sales_order(self.user, order.pk, self.shipment_data(order, key=uuid4()))
                    return pid, result["status"]
                except OperationError as exc:
                    return pid, exc.code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = [future.result(timeout=20) for future in (pool.submit(problem_worker), pool.submit(ship_worker))]
        self.assertEqual(len({pid for pid, _ in outcomes}), 2)
        order.refresh_from_db()
        payment = PaymentRecord.objects.get(order=order)
        if order.status == SalesOrder.Status.READY_TO_SHIP:
            self.assertEqual(payment.status, PaymentRecord.Status.PROBLEM)
            self.assertTrue(payment.paused)
            self.assertFalse(order.shipments.exists())
        else:
            self.assertEqual(order.status, SalesOrder.Status.SHIPPED)
            self.assertFalse(payment.paused)
            self.assertEqual(InventoryMovement.objects.filter(sales_order=order, event_kind="order_outbound").count(), 1)
            self.verify(order, actor=self.user, result="problem", problem_reason="Synthetic late mismatch")
            order.refresh_from_db()
            payment.refresh_from_db()
            self.assertEqual(order.status, SalesOrder.Status.SHIPPED)
            self.assertFalse(payment.paused)
