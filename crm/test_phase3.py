import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import close_old_connections, connection
from django.test import Client

from .models import (
    BusinessControl,
    BusinessOperation,
    Channel,
    Customer,
    CustomerAddress,
    InventoryBalance,
    SalesOrder,
    SalesOrderLine,
)
from .operations import OperationError, confirm_receipt, preview_receipt
from .permissions import ROLE_OWNER, ROLE_SALES
from .reports import build_report
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
            "note1": "Collecting",
            "note2": "Reading",
            "addresses": [{
                "recipient": "Synthetic Recipient",
                "phone": "address phone text",
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
            {"recipient": "Recipient A", "phone": "text A", "province": "北京市", "city": "北京市", "detail": "Street A"},
            {"recipient": "Recipient B", "phone": "text B", "province": "浙江省", "city": "杭州市", "detail": "Street B"},
        ])
        customer = Customer.objects.get(pk=create_customer(self.sales, data, uuid4())["customer_id"])
        self.assertEqual(customer.addresses.filter(is_active=True).count(), 2)
        self.assertFalse(customer_queryset_for(self.sales_two).filter(pk=customer.pk).exists())
        self.assertTrue(customer_queryset_for(self.owner).filter(pk=customer.pk).exists())
        duplicate_for_another_owner = self.create_customer(actor=self.sales_two, name=customer.name, contact=customer.contact)
        self.assertEqual((duplicate_for_another_owner.name, duplicate_for_another_owner.contact), (customer.name, customer.contact))

        invalid = self.customer_payload(addresses=[{
            "recipient": "Recipient", "phone": "text", "province": "北京市", "city": "上海市", "detail": "Street",
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
        self.assertEqual(
            warehouse.get("/api/v1/customers", {"include_created_today": "true"}).status_code,
            403,
        )
        self.assertEqual(warehouse.get("/customers/").status_code, 403)

    def test_customer_owner_filter_and_options_follow_scope_before_search_and_pagination(self):
        first = self.create_customer(actor=self.sales, name="Synthetic First Customer")
        second = self.create_customer(actor=self.sales_two, name="Synthetic Second Customer")
        all_customers = Client()
        all_customers.force_login(self.owner)

        response = all_customers.get("/api/v1/customers", {"page_size": 1})
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(data["total"], 2)
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["id"], first.pk)
        self.assertEqual(
            data["filter_options"]["salespeople"],
            [
                {"id": str(self.sales.pk), "label": self.sales.get_username()},
                {"id": str(self.sales_two.pk), "label": self.sales_two.get_username()},
            ],
        )
        searched = all_customers.get("/api/v1/customers", {"q": "Synthetic First", "page_size": 1}).json()
        self.assertEqual(searched["total"], 1)
        self.assertEqual(len(searched["filter_options"]["salespeople"]), 2)

        filtered = all_customers.get("/api/v1/customers", {"owner_id": self.sales_two.pk, "page_size": 1})
        self.assertEqual(filtered.status_code, 200, filtered.content)
        self.assertEqual(filtered.json()["total"], 1)
        self.assertEqual(filtered.json()["items"][0]["id"], second.pk)
        self.assertEqual(len(filtered.json()["filter_options"]["salespeople"]), 2)

        invalid = all_customers.get("/api/v1/customers", {"owner_id": "not-an-id"})
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("owner_id", invalid.json()["error"]["fields"])

        own_scope = Client()
        own_scope.force_login(self.sales)
        hidden = own_scope.get("/api/v1/customers", {"owner_id": self.sales_two.pk})
        self.assertEqual(hidden.status_code, 200)
        self.assertEqual(hidden.json()["total"], 0)
        self.assertEqual(
            hidden.json()["filter_options"]["salespeople"],
            [{"id": str(self.sales.pk), "label": self.sales.get_username()}],
        )

    def test_customer_created_today_summary_uses_local_day_and_final_filtered_queryset(self):
        now = datetime.fromisoformat("2026-10-12T12:00:00+08:00")
        customers = {
            "midnight": self.create_customer(actor=self.sales, name="Today Midnight"),
            "current": self.create_customer(actor=self.sales_two, name="Today Current"),
            "archived": self.create_customer(actor=self.sales_two, name="Today Archived"),
            "yesterday": self.create_customer(actor=self.sales, name="Yesterday"),
            "future": self.create_customer(actor=self.sales_two, name="Future Today"),
        }
        created_at = {
            "midnight": datetime.fromisoformat("2026-10-12T00:00:00+08:00"),
            "current": now,
            "archived": datetime.fromisoformat("2026-10-12T09:00:00+08:00"),
            "yesterday": datetime.fromisoformat("2026-10-11T23:59:59.999999+08:00"),
            "future": datetime.fromisoformat("2026-10-12T12:00:01+08:00"),
        }
        for key, customer in customers.items():
            Customer.objects.filter(pk=customer.pk).update(created_at=created_at[key])
        archive_customer(
            self.owner,
            customers["archived"].pk,
            {"expected_version": customers["archived"].version, "operation_key": str(uuid4())},
        )

        client = Client()
        client.force_login(self.owner)
        with patch("crm.views.timezone.localtime", return_value=now):
            default = client.get("/api/v1/customers", {"page_size": 1})
            explicit_false = client.get(
                "/api/v1/customers", {"include_created_today": "false", "page_size": 1}
            )
            self.assertEqual(default.status_code, 200, default.content)
            self.assertEqual(explicit_false.status_code, 200, explicit_false.content)
            self.assertNotIn("created_today_total", default.json())
            self.assertEqual(default.json(), explicit_false.json())

            response = client.get(
                "/api/v1/customers", {"include_created_today": "true", "page_size": 1}
            )
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()["total"], 4)
            self.assertEqual(response.json()["created_today_total"], 2)
            self.assertEqual(len(response.json()["items"]), 1)

            own_scope = Client()
            own_scope.force_login(self.sales)
            own_data = own_scope.get(
                "/api/v1/customers", {"include_created_today": "true", "page_size": 1}
            ).json()
            self.assertEqual(own_data["total"], 2)
            self.assertEqual(own_data["created_today_total"], 1)

            owner_filtered = client.get(
                "/api/v1/customers",
                {"include_created_today": "true", "owner_id": self.sales_two.pk, "page_size": 1},
            )
            self.assertEqual(owner_filtered.status_code, 200, owner_filtered.content)
            self.assertEqual(owner_filtered.json()["total"], 2)
            self.assertEqual(owner_filtered.json()["created_today_total"], 1)

            archived = client.get(
                "/api/v1/customers",
                {"include_archived": "true", "include_created_today": "true", "page_size": 1},
            )
            self.assertEqual(archived.status_code, 200, archived.content)
            self.assertEqual(archived.json()["total"], 5)
            self.assertEqual(archived.json()["created_today_total"], 3)

            filtered = client.get(
                "/api/v1/customers",
                {
                    "include_created_today": "true",
                    "q": "Today Midnight",
                    "page_size": 1,
                },
            )
            self.assertEqual(filtered.status_code, 200, filtered.content)
            self.assertEqual(filtered.json()["total"], 1)
            self.assertEqual(filtered.json()["created_today_total"], 1)

            invalid = client.get("/api/v1/customers", {"include_created_today": "yes"})
            self.assertEqual(invalid.status_code, 400)
            self.assertEqual(invalid.json()["error"]["code"], "invalid_filter")

    def test_customer_address_http_contract_and_old_order_address_switch(self):
        client = Client()
        client.force_login(self.sales)
        legacy_address = client.post(
            "/api/v1/customers",
            data=json.dumps({**self.customer_payload(addresses=[{
                "recipient": "Legacy Recipient", "contact": "legacy contact", "province": "北京市", "city": "北京市", "detail": "Legacy street",
            }]), "operation_key": str(uuid4())}),
            content_type="application/json",
        )
        self.assertEqual(legacy_address.status_code, 400)
        addresses = [
            {"recipient": "First Recipient", "phone": "first phone", "province": "北京市", "city": "北京市", "detail": "First street"},
            {"recipient": "Second Recipient", "phone": "second phone", "province": "北京市", "city": "北京市", "detail": "Second street"},
        ]
        customer_response = client.post(
            "/api/v1/customers",
            data=json.dumps({**self.customer_payload(addresses=addresses), "operation_key": str(uuid4())}),
            content_type="application/json",
        )
        self.assertEqual(customer_response.status_code, 200, customer_response.content)
        customer_id = customer_response.json()["customer_id"]
        customer_data = client.get(f"/api/v1/customers/{customer_id}").json()
        self.assertEqual([item["phone"] for item in customer_data["addresses"]], ["first phone", "second phone"])
        self.assertNotIn("contact", customer_data["addresses"][0])
        first_address, second_address = customer_data["addresses"]

        order_input = {
            "customer_id": customer_id,
            "customer_address_id": first_address["id"],
            "channel_id": self.channel.pk,
            "delivery_mode": SalesOrder.DeliveryMode.SHIPMENT,
            "lines": [{"sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk, "quantity": 2, "sale_price": "5.000"}],
        }
        order_preview = client.post("/api/v1/orders/preview", data=json.dumps(order_input), content_type="application/json")
        self.assertEqual(order_preview.status_code, 200, order_preview.content)
        order_result = client.post(
            "/api/v1/orders",
            data=json.dumps({"operation_key": str(uuid4()), "preview_token": order_preview.json()["preview_token"]}),
            content_type="application/json",
        )
        self.assertEqual(order_result.status_code, 200, order_result.content)
        order_id = order_result.json()["order_id"]
        order_data = client.get(f"/api/v1/orders/{order_id}").json()
        self.assertEqual((order_data["contact"], order_data["detail"]), ("first phone", "First street"))

        updated_customer = client.patch(
            f"/api/v1/customers/{customer_id}",
            data=json.dumps({
                "expected_version": customer_data["version"],
                "operation_key": str(uuid4()),
                "addresses": [
                    {"id": first_address["id"], "recipient": first_address["recipient"], "phone": first_address["phone"], "province": first_address["province"], "city": first_address["city"], "detail": first_address["detail"]},
                    {"id": second_address["id"], "recipient": "Updated Second Recipient", "phone": "updated second phone", "province": "北京市", "city": "北京市", "detail": "Updated second street"},
                ],
            }),
            content_type="application/json",
        )
        self.assertEqual(updated_customer.status_code, 200, updated_customer.content)
        customer_data = client.get(f"/api/v1/customers/{customer_id}").json()
        self.assertEqual(customer_data["addresses"][1]["phone"], "updated second phone")
        self.assertNotIn("contact", customer_data["addresses"][1])
        second_address = customer_data["addresses"][1]

        amendment = {
            "customer_id": customer_id,
            "customer_address_id": second_address["id"],
            "channel_id": self.channel.pk,
            "delivery_mode": SalesOrder.DeliveryMode.SHIPMENT,
            "expected_version": order_data["version"],
            "lines": [{
                "line_id": order_data["lines"][0]["line_id"],
                "sku_id": self.sku.pk,
                "warehouse_id": self.warehouse.pk,
                "quantity": 2,
                "sale_price": "5.000",
            }],
        }
        conflicting_snapshot = {
            **amendment,
            "address_snapshot": {"recipient": "Manual Recipient", "contact": "Manual phone", "province": "北京市", "city": "北京市", "detail": "Manual street"},
        }
        conflict = client.post(
            f"/api/v1/orders/{order_id}/amend/preview",
            data=json.dumps(conflicting_snapshot),
            content_type="application/json",
        )
        self.assertEqual(conflict.status_code, 400)

        address_switch_preview = client.post(
            f"/api/v1/orders/{order_id}/amend/preview",
            data=json.dumps(amendment),
            content_type="application/json",
        )
        self.assertEqual(address_switch_preview.status_code, 200, address_switch_preview.content)
        address_switch = client.post(
            f"/api/v1/orders/{order_id}/amend",
            data=json.dumps({"operation_key": str(uuid4()), "preview_token": address_switch_preview.json()["preview_token"]}),
            content_type="application/json",
        )
        self.assertEqual(address_switch.status_code, 200, address_switch.content)
        switched_order = client.get(f"/api/v1/orders/{order_id}").json()
        self.assertEqual((switched_order["contact"], switched_order["detail"]), ("updated second phone", "Updated second street"))

    def test_order_saved_address_requires_current_customer_visibility_but_keeps_snapshot_access(self):
        customer = self.create_customer(addresses=[
            {"recipient": "First Recipient", "phone": "first phone", "province": "北京市", "city": "北京市", "detail": "First street"},
        ])
        address = customer.addresses.get(is_active=True)
        order_payload = self.order_payload(
            customer_id=customer.pk,
            customer_address_id=address.pk,
            address_snapshot={},
            delivery_mode=SalesOrder.DeliveryMode.SHIPMENT,
        )
        _, _, order = self.preview_and_confirm(order_payload)
        transfer_customer(self.owner, customer.pk, {"owner_id": self.sales_two.pk, "expected_version": 1, "operation_key": str(uuid4())})
        update_customer(self.sales_two, customer.pk, {
            "addresses": [{"id": address.pk, "recipient": "Changed Recipient", "phone": "changed phone", "province": "北京市", "city": "北京市", "detail": "Changed current street"}],
            "expected_version": 2,
            "operation_key": str(uuid4()),
        })

        sales_client = Client()
        sales_client.force_login(self.sales)
        self.assertEqual(sales_client.get(f"/api/v1/customers/{customer.pk}").status_code, 404)
        hidden_address_payload = self.amendment_payload(order)
        hidden_address_payload["customer_address_id"] = address.pk
        hidden_address_payload["address_snapshot"] = {}
        denied = sales_client.post(
            f"/api/v1/orders/{order.pk}/amend/preview",
            data=json.dumps(hidden_address_payload),
            content_type="application/json",
        )
        self.assertEqual(denied.status_code, 404)
        self.assertNotIn("Changed current street", denied.content.decode())

        original_order = sales_client.get(f"/api/v1/orders/{order.pk}").json()
        self.assertEqual(original_order["detail"], "First street")
        snapshot_edit = self.amendment_payload(order)
        snapshot_edit.pop("address_snapshot")
        snapshot_edit["lines"][0]["quantity"] = 1
        snapshot_preview = sales_client.post(
            f"/api/v1/orders/{order.pk}/amend/preview",
            data=json.dumps(snapshot_edit),
            content_type="application/json",
        )
        self.assertEqual(snapshot_preview.status_code, 200, snapshot_preview.content)
        snapshot_result = sales_client.post(
            f"/api/v1/orders/{order.pk}/amend",
            data=json.dumps({"operation_key": str(uuid4()), "preview_token": snapshot_preview.json()["preview_token"]}),
            content_type="application/json",
        )
        self.assertEqual(snapshot_result.status_code, 200, snapshot_result.content)
        preserved_order = sales_client.get(f"/api/v1/orders/{order.pk}").json()
        self.assertEqual(preserved_order["detail"], "First street")
        self.assertEqual(preserved_order["lines"][0]["quantity"], 1)

        owner_client = Client()
        owner_client.force_login(self.owner)
        authorized_payload = self.amendment_payload(order)
        authorized_payload["customer_address_id"] = address.pk
        authorized_payload["address_snapshot"] = {}
        authorized_preview = owner_client.post(
            f"/api/v1/orders/{order.pk}/amend/preview",
            data=json.dumps(authorized_payload),
            content_type="application/json",
        )
        self.assertEqual(authorized_preview.status_code, 200, authorized_preview.content)

    def test_saved_address_preview_requires_view_customer_permission(self):
        customer = self.create_customer()
        address = customer.addresses.get(is_active=True)
        payload = self.order_payload(
            customer_id=customer.pk,
            customer_address_id=address.pk,
            delivery_mode=SalesOrder.DeliveryMode.SHIPMENT,
        )
        Group.objects.get(name=ROLE_SALES).permissions.remove(
            Permission.objects.get(content_type__app_label="crm", codename="view_customer")
        )
        client = Client()
        client.force_login(self.sales)
        denied_preview = client.post("/api/v1/orders/preview", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(denied_preview.status_code, 403)
        self.assertEqual(SalesOrder.objects.count(), 0)

    def test_confirm_rechecks_customer_permission_after_address_preview(self):
        customer = self.create_customer()
        address = customer.addresses.get(is_active=True)
        payload = self.order_payload(
            customer_id=customer.pk,
            customer_address_id=address.pk,
            delivery_mode=SalesOrder.DeliveryMode.SHIPMENT,
        )
        client = Client()
        client.force_login(self.sales)
        preview = client.post("/api/v1/orders/preview", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(preview.status_code, 200, preview.content)
        Group.objects.get(name=ROLE_SALES).permissions.remove(
            Permission.objects.get(content_type__app_label="crm", codename="view_customer")
        )

        confirmed = client.post(
            "/api/v1/orders",
            data=json.dumps({"operation_key": str(uuid4()), "preview_token": preview.json()["preview_token"]}),
            content_type="application/json",
        )
        self.assertEqual(confirmed.status_code, 403)
        self.assertEqual(confirmed.json()["error"]["code"], "permission_denied")
        self.assertEqual(SalesOrder.objects.count(), 0)
        self.assertEqual(InventoryBalance.objects.get(pk=self.balance.pk).occupied, 0)

    def test_birthday_routes_filters_and_legacy_fields_are_removed(self):
        self.create_customer()
        client = Client()
        client.force_login(self.sales)
        with self.assertRaises(OperationError) as error:
            self.create_customer(birthday="legacy")
        self.assertIn("birthday", error.exception.fields)
        self.assertEqual(client.get("/api/v1/birthdays").status_code, 404)
        self.assertEqual(client.get("/api/v1/me/preferences").status_code, 404)
        self.assertEqual(client.get("/birthdays/").status_code, 404)
        all_customers = client.get("/api/v1/customers").json()["items"]
        retired_filter = client.get("/api/v1/customers?birthday_today=true")
        self.assertEqual(retired_filter.status_code, 200)
        self.assertEqual(retired_filter.json()["items"], all_customers)

    def test_customer_invoice_fields_alias_search_and_order_snapshot_sync(self):
        customer = self.create_customer(
            invoice_title="档案抬头初始值",
            invoice_credit_code="初始代码",
            invoice_email="initial@example.test",
        )
        customer_client = Client()
        customer_client.force_login(self.sales)
        customer_result = customer_client.get("/api/v1/customers", {"q": "First"}).json()["items"]
        self.assertEqual(len(customer_result), 1)
        self.assertEqual(customer_result[0]["id"], customer.pk)
        self.assertEqual((customer_result[0]["note1"], customer_result[0]["note2"]), ("Collecting", "Reading"))
        self.assertEqual(customer_result[0]["invoice_title"], "档案抬头初始值")
        self.assertNotIn("birthday", customer_result[0])
        updated_customer = customer_client.patch(
            f"/api/v1/customers/{customer.pk}",
            data=json.dumps({
                "expected_version": customer.version,
                "operation_key": str(uuid4()),
                "note1": "服务端更新备注",
                "invoice_title": "档案抬头初始值",
            }),
            content_type="application/json",
        )
        self.assertEqual(updated_customer.status_code, 200, updated_customer.content)
        self.assertEqual(customer_client.get("/api/v1/customers", {"q": "First"}).json()["items"][0]["note1"], "服务端更新备注")

        self.balance.on_hand = 10
        self.balance.save(update_fields=("on_hand",))
        base = {
            "customer_id": customer.pk,
            "requires_invoice": True,
        }
        _, first_result, first_order = self.preview_and_confirm(self.order_payload(**base))
        self.assertEqual(first_result["customer_id"], customer.pk)
        self.assertEqual(first_order.invoice_title, "档案抬头初始值")

        sales_group = Group.objects.get(name=ROLE_SALES)
        change_customer = Permission.objects.get(content_type__app_label="crm", codename="change_customer")
        sales_group.permissions.remove(change_customer)
        self.sales = self.User.objects.get(pk=self.sales.pk)
        _, _, unchanged_order = self.preview_and_confirm(self.order_payload(**base))
        self.assertEqual(unchanged_order.invoice_title, "档案抬头初始值")
        with self.assertRaises(OperationError) as denied:
            preview_sales_order(self.sales, self.order_payload(**{
                **base,
                "invoice_title": "未经授权的新抬头",
            }))
        self.assertEqual(denied.exception.code, "permission_denied")

        self.sales.user_permissions.add(change_customer)
        self.sales = self.User.objects.get(pk=self.sales.pk)
        self.assertTrue(self.sales.has_perm("crm.change_customer"))
        _, _, updated_order = self.preview_and_confirm(self.order_payload(**{
            **base,
            "invoice_title": "订单新抬头",
            "invoice_credit_code": "新代码",
            "invoice_email": "new@example.test",
        }))
        customer.refresh_from_db()
        first_order.refresh_from_db()
        self.assertEqual((customer.invoice_title, customer.invoice_credit_code, customer.invoice_email), (
            "订单新抬头", "新代码", "new@example.test",
        ))
        self.assertEqual(first_order.invoice_title, "档案抬头初始值")
        self.assertEqual(updated_order.invoice_title, "订单新抬头")

    def test_customer_pages_render_for_sales(self):
        customer = self.create_customer()
        client = Client()
        client.force_login(self.sales)
        self.assertEqual(client.get("/customers/").status_code, 200)
        self.assertEqual(client.get(f"/customers/{customer.pk}/edit/").status_code, 200)


class OwnerCustomerAssignmentPhase3Tests(Phase3Base):
    def _create_customer_for(self, actor, owner, name):
        result = create_customer(actor, self.customer_payload(
            name=name,
            contact=f"contact: {name}",
            owner_id=owner.pk,
        ), uuid4())
        return Customer.objects.get(pk=result["customer_id"])

    def test_boss_and_existing_delegators_can_assign_active_owner(self):
        own_customer = self._create_customer_for(self.owner, self.owner, "Boss-owned")
        self.assertEqual(own_customer.owner_id, self.owner.pk)

        assigned_to_sales = self._create_customer_for(self.owner, self.sales_two, "Sales-owned")
        self.assertEqual(assigned_to_sales.owner_id, self.sales_two.pk)

        self.owner.groups.add(Group.objects.get(name=ROLE_SALES))
        dual_role_assignment = self._create_customer_for(self.owner, self.sales, "Dual-role assignment")
        self.assertEqual(dual_role_assignment.owner_id, self.sales.pk)

        warehouse_assignment = self._create_customer_for(self.user, self.owner, "Warehouse-assigned boss")
        self.assertEqual(warehouse_assignment.owner_id, self.owner.pk)

        admin = self.User.objects.create_superuser(
            username="admin-test", email="admin@example.test", password="test-only-password",
        )
        admin_assignment = self._create_customer_for(admin, self.owner, "Admin-assigned boss")
        self.assertEqual(admin_assignment.owner_id, self.owner.pk)

    def test_boss_and_admin_owner_options_render_in_customer_and_order_forms(self):
        admin = self.User.objects.create_superuser(
            username="admin-test", email="admin@example.test", password="test-only-password",
        )
        customer = self._create_customer_for(self.owner, self.sales, "Transfer candidate")
        for actor in (self.owner, admin):
            client = Client()
            client.force_login(actor)
            for path in (
                "/customers/",
                "/customers/new/",
                f"/customers/{customer.pk}/edit/",
                "/orders/new/",
            ):
                with self.subTest(actor=actor.username, path=path):
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200, response.content)
                    self.assertContains(
                        response,
                        f'<option value="{self.owner.pk}">{self.owner.username}</option>',
                        html=True,
                    )
        warehouse_client = Client()
        warehouse_client.force_login(self.user)
        response = warehouse_client.get("/customers/new/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertContains(
            response,
            f'<option value="{self.owner.pk}">{self.owner.username}</option>',
            html=True,
        )

    def test_owner_transfer_accepts_active_owner_and_rejects_inactive_owner(self):
        customer = self._create_customer_for(self.owner, self.sales, "Transfer to boss")
        result = transfer_customer(self.owner, customer.pk, {
            "owner_id": self.owner.pk,
            "expected_version": customer.version,
            "operation_key": str(uuid4()),
        })
        self.assertEqual(result["owner_id"], self.owner.pk)

        inactive_owner = self.User.objects.create_user(username="inactive-owner", is_active=False)
        inactive_owner.groups.add(Group.objects.get(name=ROLE_OWNER))
        with self.assertRaises(OperationError) as create_error:
            self._create_customer_for(self.user, inactive_owner, "Inactive owner target")
        self.assertEqual(create_error.exception.code, "inactive_reference")
        with self.assertRaises(OperationError) as transfer_error:
            transfer_customer(self.owner, customer.pk, {
                "owner_id": inactive_owner.pk,
                "expected_version": result["version"],
                "operation_key": str(uuid4()),
            })
        self.assertEqual(transfer_error.exception.code, "inactive_reference")

    def test_sales_cannot_assign_or_view_boss_owned_customers(self):
        customer = self._create_customer_for(self.owner, self.owner, "Private boss customer")
        client = Client()
        client.force_login(self.sales)

        response = client.get("/api/v1/customers")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn(customer.pk, {int(item["id"]) for item in response.json()["items"]})

        data = self.customer_payload(name="Sales impersonation attempt", owner_id=self.owner.pk)
        data["operation_key"] = str(uuid4())
        response = client.post("/api/v1/customers", data=json.dumps(data), content_type="application/json")
        self.assertEqual(response.status_code, 403, response.content)
        self.assertNotContains(client.get("/orders/new/"), 'id="new-customer-owner"')

    def test_owner_inline_customer_order_and_report_keep_owner_as_salesperson(self):
        self.owner.groups.add(Group.objects.get(name=ROLE_SALES))
        payload = self.order_payload(new_customer={
            "name": "Boss inline customer",
            "contact": "contact: Boss inline customer",
            "owner_id": self.owner.pk,
        })
        _, _, order = self.preview_and_confirm(payload, actor=self.owner)
        self.assertEqual(order.customer.owner_id, self.owner.pk)
        self.assertEqual(order.salesperson_id, self.owner.pk)

        report = build_report(self.owner, "sales", {"period": "all"})
        self.assertIn(
            {"id": str(self.owner.pk), "label": self.owner.get_username()},
            report["filter_options"]["salespeople"],
        )


class CustomerContactOptionalPhase3Tests(Phase3Base):
    def test_customer_and_inline_order_allow_empty_contact_but_require_name(self):
        self.assertTrue(Customer._meta.get_field("contact").blank)
        client = Client()
        client.force_login(self.sales)

        data = self.customer_payload(name="No profile contact", contact="")
        data["operation_key"] = str(uuid4())
        response = client.post("/api/v1/customers", data=json.dumps(data), content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        customer = Customer.objects.get(pk=response.json()["customer_id"])
        self.assertEqual(customer.contact, "")

        data = self.customer_payload(name="Null profile contact", contact=None)
        data["operation_key"] = str(uuid4())
        response = client.post("/api/v1/customers", data=json.dumps(data), content_type="application/json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("contact", response.json()["error"]["fields"])

        data = self.customer_payload(name="", contact="")
        data["operation_key"] = str(uuid4())
        response = client.post("/api/v1/customers", data=json.dumps(data), content_type="application/json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("name", response.json()["error"]["fields"])

        payload = self.order_payload(new_customer={"name": "Inline no contact", "contact": ""})
        _, _, order = self.preview_and_confirm(payload, actor=self.sales)
        self.assertEqual(order.customer.contact, "")
        with self.assertRaises(OperationError) as raised:
            preview_sales_order(self.sales, self.order_payload(new_customer={"name": "", "contact": ""}))
        self.assertIn("new_customer.name", raised.exception.fields)
        with self.assertRaises(OperationError) as raised:
            preview_sales_order(self.sales, self.order_payload(new_customer={"name": "Inline null", "contact": None}))
        self.assertIn("new_customer.contact", raised.exception.fields)

    def test_contact_omission_preserves_value_and_explicit_empty_clears_it(self):
        customer = self.create_customer(actor=self.sales, contact="Keep this contact")
        client = Client()
        client.force_login(self.sales)

        response = client.patch(
            f"/api/v1/customers/{customer.pk}",
            data=json.dumps({"expected_version": customer.version, "operation_key": str(uuid4()), "note1": "Changed note"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        customer.refresh_from_db()
        self.assertEqual(customer.contact, "Keep this contact")

        response = client.patch(
            f"/api/v1/customers/{customer.pk}",
            data=json.dumps({"expected_version": customer.version, "operation_key": str(uuid4()), "contact": None}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        customer.refresh_from_db()
        self.assertEqual(customer.contact, "Keep this contact")

        response = client.patch(
            f"/api/v1/customers/{customer.pk}",
            data=json.dumps({"expected_version": customer.version, "operation_key": str(uuid4()), "contact": ""}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        customer.refresh_from_db()
        self.assertEqual(customer.contact, "")

    def test_customer_and_shipping_address_contacts_remain_required(self):
        payload = self.customer_payload(
            name="Missing address phone",
            contact="",
            addresses=[{
                "recipient": "Synthetic Recipient",
                "phone": "",
                "province": "北京市",
                "city": "北京市",
                "detail": "Synthetic street 1",
            }],
        )
        with self.assertRaises(OperationError) as raised:
            create_customer(self.sales, payload, uuid4())
        self.assertIn("addresses[0].phone", raised.exception.fields)

        payload = self.order_payload(
            delivery_mode=SalesOrder.DeliveryMode.SHIPMENT,
            new_customer={"name": "Missing shipping contact", "contact": ""},
            address_snapshot={
                "recipient": "Synthetic Recipient",
                "contact": "",
                "province": "北京市",
                "city": "北京市",
                "detail": "Synthetic street 1",
            },
        )
        with self.assertRaises(OperationError) as raised:
            preview_sales_order(self.sales, payload)
        self.assertIn("address_snapshot", raised.exception.fields)

    def test_contact_is_optional_in_customer_and_order_forms(self):
        customer = self.create_customer(actor=self.sales)
        client = Client()
        client.force_login(self.sales)
        for path in ("/customers/new/", f"/customers/{customer.pk}/edit/"):
            with self.subTest(path=path):
                self.assertContains(client.get(path), '<textarea id="contact"></textarea>', html=True)
        self.assertContains(client.get("/customers/"), '<textarea name="contact"></textarea>', html=True)
        order_page = client.get("/orders/new/")
        self.assertContains(order_page, '<input id="new-customer-contact">', html=True)
        self.assertContains(order_page, "newCustomerName.required=customerMode==='new'")


class CustomerAddressOptionalPhase3Tests(Phase3Base):
    def test_customer_creation_accepts_missing_or_empty_addresses_but_rejects_partial_address(self):
        client = Client()
        client.force_login(self.sales)

        for name, addresses in (("Address key omitted", None), ("Empty address list", [])):
            data = self.customer_payload(name=name)
            if addresses is None:
                data.pop("addresses")
            else:
                data["addresses"] = addresses
            data["operation_key"] = str(uuid4())
            response = client.post("/api/v1/customers", data=json.dumps(data), content_type="application/json")
            self.assertEqual(response.status_code, 200, response.content)
            self.assertFalse(CustomerAddress.objects.filter(customer_id=response.json()["customer_id"]).exists())

        data = self.customer_payload(name="Partial address", addresses=[{"recipient": "Synthetic Recipient"}])
        data["operation_key"] = str(uuid4())
        response = client.post("/api/v1/customers", data=json.dumps(data), content_type="application/json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("addresses[0].phone", response.json()["error"]["fields"])
        self.assertFalse(Customer.objects.filter(name="Partial address").exists())

    def test_edit_omission_preserves_addresses_and_empty_list_archives_without_erasing_order_snapshot(self):
        customer = self.create_customer(actor=self.sales, name="Address history")
        address = customer.addresses.get(is_active=True)
        _, _, order = self.preview_and_confirm(self.order_payload(
            customer_id=customer.pk,
            customer_address_id=address.pk,
            delivery_mode=SalesOrder.DeliveryMode.SHIPMENT,
        ), actor=self.sales)
        snapshot = (order.recipient_snapshot, order.contact_snapshot, order.province_snapshot, order.city_snapshot, order.detail_snapshot)
        client = Client()
        client.force_login(self.sales)

        customer_data = client.get(f"/api/v1/customers/{customer.pk}").json()
        response = client.patch(
            f"/api/v1/customers/{customer.pk}",
            data=json.dumps({"expected_version": customer_data["version"], "operation_key": str(uuid4()), "note1": "No address field"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        customer_data = client.get(f"/api/v1/customers/{customer.pk}").json()
        self.assertEqual([item["id"] for item in customer_data["addresses"]], [address.pk])

        response = client.patch(
            f"/api/v1/customers/{customer.pk}",
            data=json.dumps({"expected_version": customer_data["version"], "operation_key": str(uuid4()), "addresses": []}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(client.get(f"/api/v1/customers/{customer.pk}").json()["addresses"], [])
        archived_address = CustomerAddress.objects.get(pk=address.pk)
        self.assertFalse(archived_address.is_active)
        order.refresh_from_db()
        self.assertEqual((order.recipient_snapshot, order.contact_snapshot, order.province_snapshot, order.city_snapshot, order.detail_snapshot), snapshot)

    def test_inline_new_customer_can_be_name_only_for_onsite_orders_while_shipment_still_requires_address(self):
        _, _, order = self.preview_and_confirm(
            self.order_payload(new_customer={"name": "Onsite name only"}),
            actor=self.sales,
        )
        self.assertEqual(order.customer.name, "Onsite name only")
        self.assertEqual(order.customer.contact, "")
        self.assertFalse(order.customer.addresses.exists())

        with self.assertRaises(OperationError) as raised:
            preview_sales_order(self.sales, self.order_payload(
                delivery_mode=SalesOrder.DeliveryMode.SHIPMENT,
                new_customer={"name": "Shipment profile only"},
            ))
        self.assertIn("address_snapshot", raised.exception.fields)

    def test_customer_forms_label_addresses_optional_and_start_without_blank_address_row(self):
        customer = self.create_customer(actor=self.sales)
        client = Client()
        client.force_login(self.sales)

        standalone = client.get("/customers/new/")
        self.assertContains(standalone, "收货地址（选填）")
        self.assertNotIn("if(!initial?.addresses?.length)addAddress()", standalone.content.decode())
        editor = client.get("/customers/")
        self.assertContains(editor, "收货地址（选填）")
        self.assertNotIn("if(!addressBox.children.length)addAddress()", editor.content.decode())
        self.assertContains(client.get(f"/customers/{customer.pk}/edit/"), "收货地址（选填）")


class SalesOrderPhase3Tests(Phase3Base):
    def test_changed_customer_invoice_fallback_uses_target_scope_for_confirm_and_replay(self):
        old_customer = self.create_customer(invoice_title="Old customer invoice")
        _, _, order = self.preview_and_confirm(self.order_payload(
            customer_id=old_customer.pk,
            lines=[{"sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk, "quantity": 1, "sale_price": "5.000"}],
        ))
        transfer_customer(self.owner, old_customer.pk, {
            "owner_id": self.sales_two.pk,
            "expected_version": old_customer.version,
            "operation_key": str(uuid4()),
        })
        target = self.create_customer(
            name="Visible Target Customer",
            contact="target: visible customer",
            invoice_title="Visible target invoice",
            invoice_credit_code="Visible target code",
            invoice_email="target@example.test",
        )

        payload = self.amendment_payload(order)
        payload.update({"customer_id": target.pk, "requires_invoice": True})
        client = Client()
        client.force_login(self.sales)
        preview = client.post(
            f"/api/v1/orders/{order.pk}/amend/preview",
            data=json.dumps(payload), content_type="application/json",
        )
        self.assertEqual(preview.status_code, 200, preview.content)
        self.assertEqual(preview.json()["invoice_title"], "Visible target invoice")
        self.assertEqual(preview.json()["invoice_credit_code"], "Visible target code")

        operation_key = str(uuid4())
        confirm_payload = {"operation_key": operation_key, "preview_token": preview.json()["preview_token"]}
        url = f"/api/v1/orders/{order.pk}/amend"
        confirmed = client.post(url, data=json.dumps(confirm_payload), content_type="application/json")
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        order.refresh_from_db()
        target.refresh_from_db()
        self.assertEqual((order.customer_id, order.invoice_title, order.invoice_credit_code, order.invoice_email), (
            target.pk, "Visible target invoice", "Visible target code", "target@example.test",
        ))
        replay = client.post(url, data=json.dumps(confirm_payload), content_type="application/json")
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertEqual(replay.json(), confirmed.json())

        transfer_customer(self.owner, target.pk, {
            "owner_id": self.sales_two.pk,
            "expected_version": target.version,
            "operation_key": str(uuid4()),
        })
        order.refresh_from_db()
        before = (order.version, order.customer_id, order.last_sequence, BusinessControl.objects.get(pk=1).last_sequence)
        denied_replay = client.post(url, data=json.dumps(confirm_payload), content_type="application/json")
        self.assertEqual(denied_replay.status_code, 404, denied_replay.content)
        order.refresh_from_db()
        self.assertEqual((order.version, order.customer_id, order.last_sequence, BusinessControl.objects.get(pk=1).last_sequence), before)

    def test_confirm_rejects_when_selected_invoice_fallback_customer_is_transferred(self):
        old_customer = self.create_customer(invoice_title="Still visible old invoice")
        _, _, order = self.preview_and_confirm(self.order_payload(
            customer_id=old_customer.pk,
            lines=[{"sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk, "quantity": 1, "sale_price": "5.000"}],
        ))
        target = self.create_customer(
            name="Revocable Target Customer",
            contact="target: revocable customer",
            invoice_title="Target invoice",
        )
        payload = self.amendment_payload(order)
        payload.update({"customer_id": target.pk, "requires_invoice": True})
        client = Client()
        client.force_login(self.sales)
        preview = client.post(
            f"/api/v1/orders/{order.pk}/amend/preview",
            data=json.dumps(payload), content_type="application/json",
        )
        self.assertEqual(preview.status_code, 200, preview.content)

        transfer_customer(self.owner, target.pk, {
            "owner_id": self.sales_two.pk,
            "expected_version": target.version,
            "operation_key": str(uuid4()),
        })
        order.refresh_from_db()
        before = (order.version, order.customer_id, order.last_sequence, BusinessControl.objects.get(pk=1).last_sequence)
        denied = client.post(
            f"/api/v1/orders/{order.pk}/amend",
            data=json.dumps({"operation_key": str(uuid4()), "preview_token": preview.json()["preview_token"]}),
            content_type="application/json",
        )
        self.assertEqual(denied.status_code, 404, denied.content)
        order.refresh_from_db()
        self.assertEqual((order.version, order.customer_id, order.last_sequence, BusinessControl.objects.get(pk=1).last_sequence), before)

    def test_invoice_fallback_requires_current_customer_visibility(self):
        customer = self.create_customer(
            invoice_title="Current invoice title",
            invoice_credit_code="Current credit code",
            invoice_email="current@example.test",
        )
        _, _, order = self.preview_and_confirm(self.order_payload(customer_id=customer.pk))
        payload = self.amendment_payload(order)
        payload["requires_invoice"] = True
        client = Client()
        client.force_login(self.sales)
        url = f"/api/v1/orders/{order.pk}/amend/preview"

        visible = client.post(url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(visible.status_code, 200, visible.content)
        self.assertEqual(visible.json()["invoice_title"], "Current invoice title")
        self.assertEqual(visible.json()["invoice_credit_code"], "Current credit code")

        view_customer = Permission.objects.get(content_type__app_label="crm", codename="view_customer")
        sales_group = Group.objects.get(name=ROLE_SALES)
        sales_group.permissions.remove(view_customer)
        self.sales = self.User.objects.get(pk=self.sales.pk)
        client = Client()
        client.force_login(self.sales)
        denied = client.post(url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(denied.status_code, 403)
        denied_confirm = client.post(
            f"/api/v1/orders/{order.pk}/amend",
            data=json.dumps({"operation_key": str(uuid4()), "preview_token": visible.json()["preview_token"]}),
            content_type="application/json",
        )
        self.assertEqual(denied_confirm.status_code, 403)

        self.sales.user_permissions.add(view_customer)
        self.sales = self.User.objects.get(pk=self.sales.pk)
        self.assertTrue(self.sales.has_perm("crm.view_customer"))
        customer.refresh_from_db()
        transfer_customer(self.owner, customer.pk, {
            "owner_id": self.sales_two.pk,
            "expected_version": customer.version,
            "operation_key": str(uuid4()),
        })
        customer.refresh_from_db()
        update_customer(self.sales_two, customer.pk, {
            "expected_version": customer.version,
            "operation_key": str(uuid4()),
            "invoice_title": "Transferred live title",
            "invoice_credit_code": "Transferred live code",
        })
        self.sales = self.User.objects.get(pk=self.sales.pk)
        client = Client()
        client.force_login(self.sales)
        before = (order.version, BusinessControl.objects.get(pk=1).last_sequence)
        stale_confirm = client.post(
            f"/api/v1/orders/{order.pk}/amend",
            data=json.dumps({"operation_key": str(uuid4()), "preview_token": visible.json()["preview_token"]}),
            content_type="application/json",
        )
        self.assertEqual(stale_confirm.status_code, 404, stale_confirm.content)
        hidden = client.post(url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(hidden.status_code, 404, hidden.content)
        self.assertNotIn("invoice_title", hidden.json())
        order.refresh_from_db()
        self.assertEqual((order.version, BusinessControl.objects.get(pk=1).last_sequence), before)

    def test_historical_invoice_snapshot_survives_unrelated_amendment(self):
        customer = self.create_customer(
            invoice_title="Historical title",
            invoice_credit_code="Historical code",
            invoice_email="historical@example.test",
        )
        _, _, order = self.preview_and_confirm(self.order_payload(customer_id=customer.pk, requires_invoice=True))
        customer.refresh_from_db()
        update_customer(self.sales, customer.pk, {
            "expected_version": customer.version,
            "operation_key": str(uuid4()),
            "invoice_title": "Current title",
            "invoice_credit_code": "Current code",
            "invoice_email": "current@example.test",
        })
        customer.refresh_from_db()
        current_profile = (customer.version, customer.invoice_title, customer.invoice_credit_code, customer.invoice_email)
        Group.objects.get(name=ROLE_SALES).permissions.remove(
            Permission.objects.get(content_type__app_label="crm", codename="change_customer")
        )
        self.sales = self.User.objects.get(pk=self.sales.pk)
        client = Client()
        client.force_login(self.sales)
        payload = self.amendment_payload(order)
        payload["lines"][0]["quantity"] = 1
        preview = client.post(
            f"/api/v1/orders/{order.pk}/amend/preview",
            data=json.dumps(payload), content_type="application/json",
        )
        self.assertEqual(preview.status_code, 200, preview.content)
        self.assertEqual(preview.json()["invoice_title"], "Historical title")
        operation_key = str(uuid4())
        confirmed = client.post(
            f"/api/v1/orders/{order.pk}/amend",
            data=json.dumps({"operation_key": operation_key, "preview_token": preview.json()["preview_token"]}),
            content_type="application/json",
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        customer.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual((customer.version, customer.invoice_title, customer.invoice_credit_code, customer.invoice_email), current_profile)
        self.assertEqual((order.invoice_title, order.invoice_credit_code, order.invoice_email), (
            "Historical title", "Historical code", "historical@example.test",
        ))
        self.assertFalse(BusinessOperation.objects.filter(
            action="customer_order_invoice_update", payload__order_operation_key=operation_key,
        ).exists())

        change_customer = Permission.objects.get(content_type__app_label="crm", codename="change_customer")
        self.sales.user_permissions.add(change_customer)
        self.sales = self.User.objects.get(pk=self.sales.pk)
        client = Client()
        client.force_login(self.sales)
        active_edit = self.amendment_payload(order)
        active_edit["invoice_title"] = "Intentional invoice edit"
        active_preview = client.post(
            f"/api/v1/orders/{order.pk}/amend/preview",
            data=json.dumps(active_edit), content_type="application/json",
        )
        self.assertEqual(active_preview.status_code, 200, active_preview.content)
        active_confirm = client.post(
            f"/api/v1/orders/{order.pk}/amend",
            data=json.dumps({"operation_key": str(uuid4()), "preview_token": active_preview.json()["preview_token"]}),
            content_type="application/json",
        )
        self.assertEqual(active_confirm.status_code, 200, active_confirm.content)
        customer.refresh_from_db()
        self.assertEqual((customer.invoice_title, customer.invoice_credit_code, customer.invoice_email), (
            "Intentional invoice edit", "Current code", "current@example.test",
        ))

    def test_multisku_multiwarehouse_duplicate_lines_use_rounded_unit_cost(self):
        second_sku = self.new_sku(2)
        self.add_balance(second_sku, self.second_warehouse, 3, "1.000")
        lines = [
            {"sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk, "quantity": 2, "sale_price": "1.000"},
            {"sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk, "quantity": 1, "sale_price": "1.000"},
            {"sku_id": second_sku.pk, "warehouse_id": self.second_warehouse.pk, "quantity": 1, "sale_price": "2.000"},
        ]
        preview, result, order = self.preview_and_confirm(self.order_payload(lines=lines))
        self.assertEqual([line["current_unit_cost_reference"] for line in preview["lines"]], ["0.333", "0.333", "0.333"])
        self.assertEqual(preview["cost_amount"], None)
        self.assertEqual(preview["margin_amount"], None)
        self.assertEqual(preview["commission_estimate"], None)
        self.assertTrue(all(
            line.unit_cost_snapshot is None and line.order_cost_amount is None
            for line in order.lines.filter(active=True)
        ))
        self.assertIsNone(order.cost_amount)
        self.assertIsNone(order.margin_amount)
        self.assertIsNone(order.commission_estimate)
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

    def test_order_cost_stays_blank_until_outflow_and_amendments_do_not_snapshot_references(self):
        _, _, order = self.preview_and_confirm()
        line = order.lines.get(active=True)
        line_id = line.pk
        self.assertIsNone(line.unit_cost_snapshot)
        self.assertIsNone(line.order_cost_amount)
        self.assertIsNone(order.cost_amount)

        receipt_preview = preview_receipt(self.user, self.receipt_payload(self.sku, quantity_value=3, price="3.000"))
        confirm_receipt(self.user, uuid4(), receipt_preview["preview_token"])
        line.refresh_from_db()
        self.assertIsNone(line.unit_cost_snapshot)
        self.assertIsNone(line.order_cost_amount)

        update = self.amendment_payload(order)
        update["lines"][0]["sale_price"] = "6.000"
        self.amend(order, update)
        line.refresh_from_db()
        self.assertEqual(line.pk, line_id)
        self.assertIsNone(line.unit_cost_snapshot)
        self.assertIsNone(line.order_cost_amount)
        self.assertEqual(line.cost_mode, "actual_outflow")

        reduction = self.amendment_payload(order)
        reduction["lines"][0]["quantity"] = 1
        self.amend(order, reduction)
        line.refresh_from_db()
        self.assertEqual(line.pk, line_id)
        self.assertIsNone(line.unit_cost_snapshot)
        self.assertIsNone(line.order_cost_amount)

        increase = self.amendment_payload(order)
        increase["lines"][0]["quantity"] = 2
        self.amend(order, increase)
        line.refresh_from_db()
        self.assertIsNone(line.unit_cost_snapshot)
        self.assertIsNone(line.order_cost_amount)
        self.assertEqual(line.cost_mode, "actual_outflow")
        self.assertIsNone(order.cost_amount)

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
            addresses=[{"id": address.pk, "recipient": "Updated Recipient", "phone": "new text", "province": "北京市", "city": "北京市", "detail": "New street"}],
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

    def test_cancel_api_releases_holds_once_and_renders_inline_confirmation(self):
        _, _, order = self.preview_and_confirm()
        client = Client()
        client.force_login(self.sales)

        page = client.get(f"/orders/{order.pk}/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'id="cancel"')
        self.assertContains(page, 'id="cancel-confirmation" hidden')
        self.assertContains(page, 'id="confirm-cancel"')
        self.assertContains(page, 'id="cancel-back"')
        self.assertContains(page, "确认后将取消订单并释放该订单占用的库存。")

        control = BusinessControl.objects.get(pk=1)
        sequence_before = control.last_sequence
        balance = InventoryBalance.objects.get(pk=self.balance.pk)
        self.assertEqual(balance.occupied, 2)
        payload = {"expected_version": order.version, "operation_key": str(uuid4())}
        first = client.post(
            f"/api/v1/orders/{order.pk}/cancel",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(first.status_code, 200)
        first_result = first.json()
        self.assertEqual(first_result["status"], SalesOrder.Status.CANCELLED)
        order.refresh_from_db()
        balance.refresh_from_db()
        control.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.Status.CANCELLED)
        self.assertEqual(balance.occupied, 0)
        self.assertEqual(control.last_sequence, sequence_before + 1)
        self.assertEqual(
            BusinessOperation.objects.filter(sales_order=order, action="sales_order_cancel").count(),
            1,
        )

        replay = client.post(
            f"/api/v1/orders/{order.pk}/cancel",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json(), first_result)
        balance.refresh_from_db()
        control.refresh_from_db()
        self.assertEqual(balance.occupied, 0)
        self.assertEqual(control.last_sequence, sequence_before + 1)
        self.assertEqual(
            BusinessOperation.objects.filter(sales_order=order, action="sales_order_cancel").count(),
            1,
        )

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

    def test_order_sku_search_and_preview_keep_inactive_sku_restriction(self):
        self.sku.is_active = False
        self.sku.save(update_fields=("is_active",))
        client = Client()
        client.force_login(self.sales)

        skus = client.get("/api/v1/skus").json()["items"]
        self.assertNotIn(self.sku.pk, {item["id"] for item in skus})
        with self.assertRaises(OperationError) as error:
            preview_sales_order(self.sales, self.order_payload())
        self.assertEqual(error.exception.code, "inactive_reference")

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

    def test_order_new_customer_invoice_is_atomic_and_idempotent(self):
        payload = self.order_payload(
            delivery_mode=SalesOrder.DeliveryMode.SHIPMENT,
            address_snapshot={
                "recipient": "收件人甲",
                "contact": "收件电话甲",
                "province": "上海市",
                "city": "上海市",
                "detail": "合成路 1 号",
            },
            new_customer={"name": "档案客户乙", "contact": "客户电话乙"},
            requires_invoice=True,
            invoice_title="开票抬头乙",
            invoice_credit_code="合成代码乙",
            invoice_email="invoice@example.test",
        )
        with self.assertRaises(OperationError) as walk_in:
            preview_sales_order(self.sales, self.order_payload(
                requires_invoice=True,
                invoice_title="散客不应开票",
            ))
        self.assertIn("requires_invoice", walk_in.exception.fields)
        with self.assertRaises(OperationError) as address_input:
            preview_sales_order(self.sales, self.order_payload(**{
                **payload,
                "new_customer": {"name": "档案客户乙", "contact": "客户电话乙", "addresses": []},
            }))
        self.assertIn("new_customer.addresses", address_input.exception.fields)
        with self.assertRaises(OperationError) as customer_source:
            preview_sales_order(self.sales, self.order_payload(**{**payload, "customer_id": 9999}))
        self.assertIn("new_customer", customer_source.exception.fields)
        preview = preview_sales_order(self.sales, payload)
        key = uuid4()
        manager = SalesOrderLine.objects
        real_create = manager.create

        with patch.object(manager, "create", side_effect=RuntimeError("synthetic order-line failure")):
            with self.assertRaisesRegex(RuntimeError, "order-line failure"):
                confirm_sales_order(self.sales, key, preview["preview_token"])

        self.assertEqual(Customer.objects.count(), 0)
        self.assertEqual(CustomerAddress.objects.count(), 0)
        self.assertEqual(SalesOrder.objects.count(), 0)
        self.assertEqual(BusinessOperation.objects.count(), 0)
        self.assertEqual(InventoryBalance.objects.get(pk=self.balance.pk).occupied, 0)

        with patch.object(manager, "create", wraps=real_create):
            result = confirm_sales_order(self.sales, key, preview["preview_token"])
        replay = confirm_sales_order(self.sales, key, preview["preview_token"])
        self.assertEqual(replay, result)
        self.assertEqual(Customer.objects.count(), 1)
        self.assertEqual(CustomerAddress.objects.count(), 1)
        customer = Customer.objects.get(pk=result["customer_id"])
        address = CustomerAddress.objects.get(customer=customer)
        order = SalesOrder.objects.get(pk=result["order_id"])
        self.assertEqual((customer.name, customer.contact), ("档案客户乙", "客户电话乙"))
        self.assertEqual((address.recipient, address.contact), ("收件人甲", "收件电话甲"))
        self.assertEqual((order.contact_snapshot, order.invoice_title, order.invoice_credit_code, order.invoice_email), (
            "收件电话甲", "开票抬头乙", "合成代码乙", "invoice@example.test",
        ))
        customer.invoice_title = "后续档案抬头"
        customer.save(update_fields=("invoice_title",))
        order.refresh_from_db()
        self.assertEqual(order.invoice_title, "开票抬头乙")

    def test_inventory_order_context_adds_back_only_visible_active_order_occupancy(self):
        _, _, order = self.preview_and_confirm()
        client = Client()
        client.force_login(self.sales)

        ordinary = client.get("/api/v1/inventory", {"sku_id": self.sku.pk})
        contextual = client.get("/api/v1/inventory", {"sku_id": self.sku.pk, "order_id": order.pk})
        self.assertEqual(ordinary.status_code, 200)
        self.assertEqual(contextual.status_code, 200)
        ordinary_row = ordinary.json()["items"][0]
        contextual_row = contextual.json()["items"][0]
        self.assertEqual(ordinary_row["available"], 1)
        self.assertNotIn("available_for_order", ordinary_row)
        self.assertEqual(contextual_row["available"], 1)
        self.assertEqual(contextual_row["available_for_order"], 3)

        other_sales = Client()
        other_sales.force_login(self.sales_two)
        denied = other_sales.get("/api/v1/inventory", {"sku_id": self.sku.pk, "order_id": order.pk})
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(denied.json()["error"]["code"], "not_found")

    def _replay_order_in_new_session(self, key, preview_token):
        client = Client()
        client.force_login(self.User.objects.get(pk=self.sales.pk))
        return client.post(
            "/api/v1/orders",
            data=json.dumps({"operation_key": str(key), "preview_token": preview_token}),
            content_type="application/json",
        )

    def test_invoice_update_order_replay_requires_current_change_permission(self):
        customer = self.create_customer(invoice_title="原抬头")
        preview = preview_sales_order(self.sales, self.order_payload(
            customer_id=customer.pk,
            requires_invoice=True,
            invoice_title="更新后的抬头",
        ))
        key = uuid4()
        result = confirm_sales_order(self.sales, key, preview["preview_token"])
        customer.refresh_from_db()
        self.assertEqual(customer.invoice_title, "更新后的抬头")
        self.assertTrue(BusinessOperation.objects.filter(
            action="customer_order_invoice_update",
            payload__order_operation_key=str(key),
            customer=customer,
        ).exists())

        Group.objects.get(name=ROLE_SALES).permissions.remove(
            Permission.objects.get(content_type__app_label="crm", codename="change_customer")
        )
        before = (
            customer.version,
            customer.invoice_title,
            SalesOrder.objects.count(),
            BusinessOperation.objects.count(),
            InventoryBalance.objects.get(pk=self.balance.pk).occupied,
            BusinessControl.objects.get(pk=1).last_sequence,
        )
        replay = self._replay_order_in_new_session(key, preview["preview_token"])
        self.assertEqual(replay.status_code, 403)
        self.assertEqual(replay.json()["error"]["code"], "permission_denied")
        customer.refresh_from_db()
        self.assertEqual((
            customer.version,
            customer.invoice_title,
            SalesOrder.objects.count(),
            BusinessOperation.objects.count(),
            InventoryBalance.objects.get(pk=self.balance.pk).occupied,
            BusinessControl.objects.get(pk=1).last_sequence,
        ), before)
        self.assertEqual(result["order_id"], SalesOrder.objects.get().pk)

    def test_invoice_unchanged_order_replay_needs_no_change_permission(self):
        customer = self.create_customer(invoice_title="原抬头")
        preview = preview_sales_order(self.sales, self.order_payload(
            customer_id=customer.pk,
            requires_invoice=True,
            invoice_title="原抬头",
        ))
        key = uuid4()
        result = confirm_sales_order(self.sales, key, preview["preview_token"])
        self.assertFalse(BusinessOperation.objects.filter(
            action="customer_order_invoice_update",
            payload__order_operation_key=str(key),
        ).exists())
        before = (
            SalesOrder.objects.count(),
            BusinessOperation.objects.count(),
            InventoryBalance.objects.get(pk=self.balance.pk).occupied,
            BusinessControl.objects.get(pk=1).last_sequence,
        )
        Group.objects.get(name=ROLE_SALES).permissions.remove(
            Permission.objects.get(content_type__app_label="crm", codename="change_customer")
        )

        replay = self._replay_order_in_new_session(key, preview["preview_token"])
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertEqual(replay.json(), result)
        self.assertEqual((
            SalesOrder.objects.count(),
            BusinessOperation.objects.count(),
            InventoryBalance.objects.get(pk=self.balance.pk).occupied,
            BusinessControl.objects.get(pk=1).last_sequence,
        ), before)

    def test_invoice_update_order_replay_requires_current_customer_scope(self):
        customer = self.create_customer(invoice_title="原抬头")
        preview = preview_sales_order(self.sales, self.order_payload(
            customer_id=customer.pk,
            requires_invoice=True,
            invoice_title="更新后的抬头",
        ))
        key = uuid4()
        confirm_sales_order(self.sales, key, preview["preview_token"])
        customer.refresh_from_db()
        transfer_customer(self.owner, customer.pk, {
            "owner_id": self.sales_two.pk,
            "expected_version": customer.version,
            "operation_key": str(uuid4()),
        })
        replay_user = self.User.objects.get(pk=self.sales.pk)
        self.assertTrue(replay_user.has_perm("crm.change_customer"))
        self.assertFalse(customer_queryset_for(replay_user, changing=True).filter(pk=customer.pk).exists())
        before = (
            SalesOrder.objects.count(),
            BusinessOperation.objects.count(),
            InventoryBalance.objects.get(pk=self.balance.pk).occupied,
            BusinessControl.objects.get(pk=1).last_sequence,
        )

        replay = self._replay_order_in_new_session(key, preview["preview_token"])
        self.assertEqual(replay.status_code, 404)
        self.assertEqual(replay.json()["error"]["code"], "not_found")
        self.assertEqual((
            SalesOrder.objects.count(),
            BusinessOperation.objects.count(),
            InventoryBalance.objects.get(pk=self.balance.pk).occupied,
            BusinessControl.objects.get(pk=1).last_sequence,
        ), before)
