import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import close_old_connections, connection
from django.test import Client
from django.utils import timezone
from zoneinfo import ZoneInfo

from .models import (
    AccountProfile,
    BusinessControl,
    BusinessOperation,
    Channel,
    Customer,
    InventoryBalance,
    SalesOrder,
    SalesOrderLine,
)
from .operations import OperationError, confirm_receipt, preview_receipt
from .permissions import ROLE_OWNER, ROLE_SALES
from .sales import (
    archive_customer,
    cancel_sales_order,
    confirm_sales_order,
    create_customer,
    customer_queryset_for,
    preview_sales_order,
    sales_order_queryset_for,
    transfer_customer,
    update_customer,
)
from .test_phase2 import Phase2Base


class Phase3Base(Phase2Base):
    def setUp(self):
        super().setUp()
        self.sales = self.User.objects.create_user(username="sales-one", password="test-only-password")
        self.sales.groups.add(Group.objects.get(name=ROLE_SALES))
        self.sales_two = self.User.objects.create_user(username="sales-two", password="test-only-password")
        self.sales_two.groups.add(Group.objects.get(name=ROLE_SALES))
        self.channel = Channel.objects.create(name="Synthetic Online")
        self.balance = self.add_balance(self.sku, self.warehouse, 3, "1.000")

    def add_balance(self, sku, warehouse, quantity, cost):
        return InventoryBalance.objects.create(
            sku=sku,
            warehouse=warehouse,
            on_hand=quantity,
            cost_amount=Decimal(cost),
        )

    def customer_payload(self, **overrides):
        payload = {
            "name": "Synthetic Customer",
            "contact": "wechat: customer text, not a phone number",
            "company": "Synthetic Company",
            "aliases": ["First", "First"],
            "birthday": None,
            "interests": "Collecting",
            "hobbies": "Reading",
            "addresses": [{
                "recipient": "Synthetic Recipient",
                "contact": "contact text",
                "province": "北京市",
                "city": "北京市",
                "detail": "Synthetic street 1",
            }],
        }
        payload.update(overrides)
        return payload

    def create_customer(self, actor=None, **overrides):
        actor = actor or self.sales
        data = self.customer_payload(**overrides)
        if actor == self.owner:
            data["owner_id"] = self.sales.pk
        result = create_customer(actor, data, uuid4())
        return Customer.objects.get(pk=result["customer_id"])

    def order_payload(self, lines=None, **overrides):
        payload = {
            "channel_id": self.channel.pk,
            "delivery_mode": SalesOrder.DeliveryMode.ON_SITE,
            "address_snapshot": {},
            "remarks": "Synthetic order",
            "lines": lines or [{
                "sku_id": self.sku.pk,
                "warehouse_id": self.warehouse.pk,
                "quantity": 2,
                "sale_price": "5.000",
            }],
        }
        payload.update(overrides)
        return payload

    def preview_and_confirm(self, payload=None, *, actor=None, key=None):
        actor = actor or self.sales
        preview = preview_sales_order(actor, payload or self.order_payload())
        result = confirm_sales_order(actor, key or uuid4(), preview["preview_token"])
        return preview, result, SalesOrder.objects.get(pk=result["order_id"])

    def amendment_payload(self, order):
        order.refresh_from_db()
        return {
            "expected_version": order.version,
            "channel_id": order.channel_id,
            "customer_id": order.customer_id,
            "delivery_mode": order.delivery_mode,
            "address_snapshot": {
                "recipient": order.recipient_snapshot,
                "contact": order.contact_snapshot,
                "province": order.province_snapshot,
                "city": order.city_snapshot,
                "detail": order.detail_snapshot,
            },
            "remarks": order.remarks,
            "lines": [{
                "line_id": line.pk,
                "sku_id": line.sku_id,
                "warehouse_id": line.warehouse_id,
                "quantity": line.quantity,
                "sale_price": str(line.unit_price),
            } for line in order.lines.filter(active=True).order_by("line_number")],
        }

    def amend(self, order, payload):
        preview = preview_sales_order(self.sales, payload, order_id=order.pk)
        return confirm_sales_order(self.sales, uuid4(), preview["preview_token"])


class CustomerPhase3Tests(Phase3Base):
    def test_customer_addresses_region_validation_scope_transfer_and_archive(self):
        data = self.customer_payload(addresses=[
            {"recipient": "Recipient A", "contact": "text A", "province": "北京市", "city": "北京市", "detail": "Street A"},
            {"recipient": "Recipient B", "contact": "text B", "province": "浙江省", "city": "杭州市", "detail": "Street B"},
        ])
        customer = Customer.objects.get(pk=create_customer(self.sales, data, uuid4())["customer_id"])
        self.assertEqual(customer.addresses.filter(is_active=True).count(), 2)
        self.assertFalse(customer_queryset_for(self.sales_two).filter(pk=customer.pk).exists())
        self.assertTrue(customer_queryset_for(self.owner).filter(pk=customer.pk).exists())
        duplicate_for_another_owner = self.create_customer(actor=self.sales_two, name=customer.name, contact=customer.contact)
        self.assertEqual((duplicate_for_another_owner.name, duplicate_for_another_owner.contact), (customer.name, customer.contact))

        invalid = self.customer_payload(addresses=[{
            "recipient": "Recipient", "contact": "text", "province": "北京市", "city": "上海市", "detail": "Street",
        }])
        with self.assertRaisesRegex(OperationError, "省市不匹配"):
            create_customer(self.sales, invalid, uuid4())
        self.assertEqual(Customer.objects.count(), 2)

        with self.assertRaisesRegex(OperationError, "权限"):
            transfer_customer(self.sales, customer.pk, {"owner_id": self.sales_two.pk, "expected_version": 1, "operation_key": str(uuid4())})
        result = transfer_customer(self.owner, customer.pk, {"owner_id": self.sales_two.pk, "expected_version": 1, "operation_key": str(uuid4())})
        self.assertEqual(result["owner_id"], self.sales_two.pk)
        self.assertTrue(customer_queryset_for(self.sales_two).filter(pk=customer.pk).exists())
        self.assertFalse(customer_queryset_for(self.sales).filter(pk=customer.pk).exists())

        archived = archive_customer(self.owner, customer.pk, {"expected_version": 2, "operation_key": str(uuid4())})
        self.assertFalse(archived["is_active"])
        self.assertFalse(customer_queryset_for(self.sales_two).filter(pk=customer.pk, is_active=True).exists())
        self.assertTrue(Customer.objects.filter(pk=customer.pk).exists())

    def test_customer_api_enforces_authentication_and_owner_scope(self):
        customer = self.create_customer()
        anonymous = Client()
        self.assertEqual(anonymous.get("/api/v1/customers").status_code, 401)

        other = Client()
        other.force_login(self.sales_two)
        self.assertEqual(other.get(f"/api/v1/customers/{customer.pk}").status_code, 404)
        self.assertEqual(other.get("/api/v1/customers").json()["total"], 0)

        warehouse = Client()
        warehouse.force_login(self.user)
        self.assertEqual(warehouse.get("/api/v1/customers").status_code, 403)
        self.assertEqual(warehouse.get("/customers/").status_code, 403)

    def test_birthday_today_is_shanghai_personal_and_february_29_exact(self):
        leap = self.create_customer(birthday="2000-02-29", name="Leap Customer")
        ordinary = self.create_customer(actor=self.sales_two, birthday="2000-02-28", name="Ordinary Customer")
        client = Client()
        client.force_login(self.sales)
        with patch("crm.views.timezone.localdate", return_value=date(2025, 2, 28)):
            self.assertEqual(client.get("/api/v1/birthdays").json()["items"], [])
        with patch("crm.views.timezone.localdate", return_value=date(2024, 2, 29)):
            birthday_list = client.get("/api/v1/birthdays").json()
            self.assertEqual([item["id"] for item in birthday_list["items"]], [leap.pk])

        response = client.patch(
            "/api/v1/me/preferences",
            data=json.dumps({"birthday_reminder_enabled": False, "expected_version": 1, "operation_key": str(uuid4())}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        with patch("crm.views.timezone.localdate", return_value=date(2024, 2, 29)):
            disabled = client.get("/api/v1/birthdays").json()
            self.assertFalse(disabled["birthday_reminder_enabled"])
            self.assertEqual(disabled["items"], [])
        self.assertTrue(Customer.objects.filter(pk=leap.pk, birthday=date(2000, 2, 29)).exists())
        self.assertTrue(AccountProfile.objects.get(user=self.sales).birthday_reminder_enabled is False)
        self.assertFalse(AccountProfile.objects.filter(user=self.sales_two).exists())
        self.assertTrue(ordinary.birthday)
        reenabled = client.patch(
            "/api/v1/me/preferences",
            data=json.dumps({"birthday_reminder_enabled": True, "expected_version": 2, "operation_key": str(uuid4())}),
            content_type="application/json",
        )
        self.assertEqual(reenabled.status_code, 200)
        with patch("crm.views.timezone.localdate", return_value=date(2024, 2, 29)):
            self.assertEqual([item["id"] for item in client.get("/api/v1/birthdays").json()["items"]], [leap.pk])

    def test_birthday_today_uses_shanghai_midnight(self):
        customer = self.create_customer(birthday="2000-03-01", name="Shanghai Midnight Customer")
        client = Client()
        client.force_login(self.sales)
        instant = datetime(2025, 2, 28, 16, 30, tzinfo=ZoneInfo("UTC"))
        with timezone.override(ZoneInfo("Asia/Shanghai")), patch("crm.views.timezone.now", return_value=instant):
            response = client.get("/api/v1/birthdays").json()
        self.assertEqual(response["date"], "2025-03-01")
        self.assertEqual([item["id"] for item in response["items"]], [customer.pk])

    def test_birthday_requires_canonical_iso_date(self):
        with self.assertRaises(OperationError) as error:
            self.create_customer(birthday="20000229")
        self.assertIn("YYYY-MM-DD", error.exception.fields["birthday"])

    def test_customer_edit_and_birthday_pages_render_for_sales(self):
        customer = self.create_customer()
        client = Client()
        client.force_login(self.sales)
        self.assertEqual(client.get("/customers/").status_code, 200)
        self.assertEqual(client.get(f"/customers/{customer.pk}/edit/").status_code, 200)
        self.assertEqual(client.get("/birthdays/").status_code, 200)


class SalesOrderPhase3Tests(Phase3Base):
    def test_multisku_multiwarehouse_duplicate_lines_use_rounded_unit_cost(self):
        second_sku = self.new_sku(2)
        self.add_balance(second_sku, self.second_warehouse, 3, "1.000")
        lines = [
            {"sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk, "quantity": 2, "sale_price": "1.000"},
            {"sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk, "quantity": 1, "sale_price": "1.000"},
            {"sku_id": second_sku.pk, "warehouse_id": self.second_warehouse.pk, "quantity": 1, "sale_price": "2.000"},
        ]
        preview, result, order = self.preview_and_confirm(self.order_payload(lines=lines))
        self.assertEqual([line["unit_cost_snapshot"] for line in preview["lines"]], ["0.333", "0.333", "0.333"])
        self.assertEqual(preview["cost_amount"], "1.332")
        self.assertEqual(order.lines.filter(active=True).count(), 3)
        self.assertEqual(InventoryBalance.objects.get(pk=self.balance.pk).occupied, 3)
        self.assertEqual(InventoryBalance.objects.get(warehouse=self.second_warehouse, sku=second_sku).occupied, 1)
        self.assertEqual(result["status"], SalesOrder.Status.AWAITING_PAYMENT)

    def test_duplicate_sku_rows_cannot_over_sell(self):
        lines = [
            {"sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk, "quantity": 2, "sale_price": "1.000"},
            {"sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk, "quantity": 2, "sale_price": "1.000"},
        ]
        with self.assertRaises(OperationError) as error:
            preview_sales_order(self.sales, self.order_payload(lines=lines))
        self.assertEqual(error.exception.code, "stock_conflict")
        self.assertEqual(InventoryBalance.objects.get(pk=self.balance.pk).occupied, 0)
        self.assertEqual(SalesOrder.objects.count(), 0)

    def test_receipt_does_not_change_order_cost_and_amend_rules_keep_or_recalculate_cost(self):
        _, _, order = self.preview_and_confirm()
        line = order.lines.get(active=True)
        line_id = line.pk
        self.assertEqual(line.unit_cost_snapshot, Decimal("0.333"))

        receipt_preview = preview_receipt(self.user, self.receipt_payload(self.sku, quantity_value=3, price="3.000"))
        confirm_receipt(self.user, uuid4(), receipt_preview["preview_token"])
        line.refresh_from_db()
        self.assertEqual(line.unit_cost_snapshot, Decimal("0.333"))

        update = self.amendment_payload(order)
        update["lines"][0]["sale_price"] = "6.000"
        self.amend(order, update)
        line.refresh_from_db()
        self.assertEqual(line.pk, line_id)
        self.assertEqual(line.unit_cost_snapshot, Decimal("0.333"))
        self.assertEqual(line.cost_mode, "price_only")

        reduction = self.amendment_payload(order)
        reduction["lines"][0]["quantity"] = 1
        self.amend(order, reduction)
        line.refresh_from_db()
        self.assertEqual(line.pk, line_id)
        self.assertEqual(line.unit_cost_snapshot, Decimal("0.333"))
        self.assertEqual(line.order_cost_amount, Decimal("0.333"))

        increase = self.amendment_payload(order)
        increase["lines"][0]["quantity"] = 2
        self.amend(order, increase)
        line.refresh_from_db()
        self.assertEqual(line.unit_cost_snapshot, Decimal("1.667"))
        self.assertEqual(line.order_cost_amount, Decimal("3.334"))
        self.assertEqual(line.cost_mode, "average_recalculation")

    def test_customer_and_address_snapshots_survive_edit_transfer_and_archive(self):
        customer = self.create_customer()
        address = customer.addresses.get(is_active=True)
        payload = self.order_payload(
            customer_id=customer.pk,
            customer_address_id=address.pk,
            address_snapshot={},
            delivery_mode=SalesOrder.DeliveryMode.SHIPMENT,
        )
        _, _, order = self.preview_and_confirm(payload)
        self.assertEqual(order.customer_name_snapshot, customer.name)
        self.assertEqual(order.detail_snapshot, address.detail)

        edit = self.customer_payload(
            name="Renamed Customer",
            addresses=[{"id": address.pk, "recipient": "Updated Recipient", "contact": "new text", "province": "北京市", "city": "北京市", "detail": "New street"}],
        )
        edit.update({"expected_version": customer.version, "operation_key": str(uuid4())})
        update_customer(self.sales, customer.pk, edit)
        transfer_customer(self.owner, customer.pk, {"owner_id": self.sales_two.pk, "expected_version": 2, "operation_key": str(uuid4())})
        archive_customer(self.owner, customer.pk, {"expected_version": 3, "operation_key": str(uuid4())})
        order.refresh_from_db()
        self.assertEqual(order.salesperson_id, self.sales.pk)
        self.assertEqual(order.customer_name_snapshot, "Synthetic Customer")
        self.assertEqual(order.recipient_snapshot, "Synthetic Recipient")
        self.assertEqual(order.detail_snapshot, "Synthetic street 1")
        self.assertEqual(order.customer.owner_id, self.sales_two.pk)

    def test_idempotent_confirm_key_conflict_and_cancel_release(self):
        payload = self.order_payload()
        preview = preview_sales_order(self.sales, payload)
        key = uuid4()
        first = confirm_sales_order(self.sales, key, preview["preview_token"])
        replay = confirm_sales_order(self.sales, key, preview["preview_token"])
        self.assertEqual(replay, first)

        changed = self.order_payload(lines=[{"sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk, "quantity": 1, "sale_price": "9.000"}])
        other_preview = preview_sales_order(self.sales, changed)
        with self.assertRaises(OperationError) as error:
            confirm_sales_order(self.sales, key, other_preview["preview_token"])
        self.assertEqual(error.exception.code, "idempotency_conflict")

        cancellation = {"expected_version": first["version"], "operation_key": str(uuid4())}
        cancelled = cancel_sales_order(self.sales, first["order_id"], cancellation)
        self.assertEqual(cancelled["status"], SalesOrder.Status.CANCELLED)
        self.assertEqual(InventoryBalance.objects.get(pk=self.balance.pk).occupied, 0)
        self.assertEqual(cancel_sales_order(self.sales, first["order_id"], cancellation), cancelled)
        with self.assertRaises(OperationError) as error:
            cancel_sales_order(self.sales, first["order_id"], {"expected_version": cancelled["version"], "operation_key": str(uuid4())})
        self.assertEqual(error.exception.code, "state_conflict")

    def test_stale_amendment_preview_leaves_existing_holds_untouched(self):
        _, _, order = self.preview_and_confirm()
        update = self.amendment_payload(order)
        update["lines"][0]["quantity"] = 1
        preview = preview_sales_order(self.sales, update, order_id=order.pk)
        # A second confirmed order advances the shared sequence while consuming the last available unit.
        self.preview_and_confirm(self.order_payload(lines=[{
            "sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk, "quantity": 1, "sale_price": "2.000",
        }]), actor=self.sales_two)
        with self.assertRaises(OperationError) as error:
            confirm_sales_order(self.sales, uuid4(), preview["preview_token"])
        self.assertEqual(error.exception.code, "stale_preview")
        order.refresh_from_db()
        self.assertEqual(order.lines.get(active=True).quantity, 2)
        self.assertEqual(InventoryBalance.objects.get(pk=self.balance.pk).occupied, 3)

    def test_reference_change_invalidates_order_preview(self):
        preview = preview_sales_order(self.sales, self.order_payload())
        Channel.objects.filter(pk=self.channel.pk).update(name="Renamed Channel")
        with self.assertRaises(OperationError) as error:
            confirm_sales_order(self.sales, uuid4(), preview["preview_token"])
        self.assertEqual(error.exception.code, "stale_preview")
        self.assertEqual(SalesOrder.objects.count(), 0)
        self.assertEqual(InventoryBalance.objects.get(pk=self.balance.pk).occupied, 0)

    def test_second_line_failure_rolls_back_and_same_key_can_retry(self):
        second_sku = self.new_sku(2)
        self.add_balance(second_sku, self.second_warehouse, 3, "3.000")
        payload = self.order_payload(lines=[
            {"sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk, "quantity": 1, "sale_price": "2.000"},
            {"sku_id": second_sku.pk, "warehouse_id": self.second_warehouse.pk, "quantity": 1, "sale_price": "3.000"},
        ])
        preview = preview_sales_order(self.sales, payload)
        key = uuid4()
        manager = SalesOrderLine.objects
        real_create = manager.create
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("synthetic second order line failure")
            return real_create(*args, **kwargs)

        with patch.object(manager, "create", side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, "second order line"):
                confirm_sales_order(self.sales, key, preview["preview_token"], request_id="phase3.rollback")
        self.assertEqual(SalesOrder.objects.count(), 0)
        self.assertEqual(SalesOrderLine.objects.count(), 0)
        self.assertEqual(BusinessOperation.objects.count(), 0)
        self.assertEqual(BusinessControl.objects.get(pk=1).last_sequence, 0)
        self.assertEqual(InventoryBalance.objects.get(pk=self.balance.pk).occupied, 0)
        result = confirm_sales_order(self.sales, key, preview["preview_token"])
        self.assertEqual(SalesOrder.objects.get(pk=result["order_id"]).lines.filter(active=True).count(), 2)

    def test_competing_postgresql_connections_cannot_oversell(self):
        payload = self.order_payload(lines=[{
            "sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk, "quantity": 2, "sale_price": "5.000",
        }])
        previews = [preview_sales_order(self.sales, payload), preview_sales_order(self.sales_two, payload)]
        barrier = threading.Barrier(2)

        def worker(index):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    backend_pid = cursor.fetchone()[0]
                barrier.wait(timeout=10)
                try:
                    result = confirm_sales_order(
                        self.sales if index == 0 else self.sales_two,
                        uuid4(),
                        previews[index]["preview_token"],
                    )
                    return backend_pid, result["order_id"]
                except OperationError as exc:
                    return backend_pid, exc.code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(worker, (0, 1)))
        self.assertEqual(len({pid for pid, _ in outcomes}), 2)
        results = [value for _, value in outcomes]
        self.assertEqual(sum(isinstance(value, int) for value in results), 1)
        self.assertEqual(results.count("stale_preview"), 1)
        self.assertEqual(InventoryBalance.objects.get(pk=self.balance.pk).occupied, 2)
        self.assertEqual(SalesOrder.objects.count(), 1)

    def test_order_pages_and_warehouse_view_scope(self):
        _, _, order = self.preview_and_confirm()
        sales_client = Client()
        sales_client.force_login(self.sales)
        self.assertEqual(sales_client.get("/orders/").status_code, 200)
        self.assertEqual(sales_client.get(f"/orders/{order.pk}/").status_code, 200)
        order_form = sales_client.get("/orders/new/")
        self.assertEqual(order_form.status_code, 200)
        self.assertContains(order_form, 'id="customer-query"')
        self.assertContains(order_form, 'id="sku-query"')
        self.assertContains(order_form, 'id="warehouse-query"')
        self.assertEqual(sales_client.get(f"/orders/{order.pk}/edit/").status_code, 200)

        warehouse = Client()
        warehouse.force_login(self.user)
        self.assertEqual(warehouse.get("/orders/").status_code, 200)
        self.assertTrue(sales_order_queryset_for(self.user).filter(pk=order.pk).exists())
        self.assertFalse(sales_order_queryset_for(self.sales_two).filter(pk=order.pk).exists())
