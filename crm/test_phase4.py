from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal
from io import BytesIO
import json
import tempfile
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection
from django.test import Client, override_settings
from django.utils import timezone
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
    BusinessControl,
    BusinessOperation,
    CommissionAdjustment,
    CommissionReview,
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
from .sales import cancel_sales_order, confirm_sales_order, create_customer, preview_sales_order
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


class D15PermissionBoundaryTests(Phase4Base):
    def new_sales_user(self, username, *permissions):
        user = User.objects.create_user(username=username, password="test-only-password")
        user.groups.add(Group.objects.get(name=ROLE_SALES))
        granted = Permission.objects.filter(content_type__app_label="crm", codename__in=permissions)
        user.user_permissions.add(*granted)
        return user

    def set_commission_marker(self, order):
        order.commission_estimate = Decimal("987.654")
        order.save(update_fields=("commission_estimate",))
        now = timezone.now()
        CommissionReview.objects.create(
            order=order,
            approved_amount=Decimal("876.543"),
            approved_amount_version=order.amount_version,
            first_earned_at=now,
            reviewer=self.owner,
            reviewed_at=now,
        )
        approval_operation = BusinessOperation.objects.filter(sales_order=order).order_by("sequence", "id").first()
        CommissionAdjustment.objects.create(
            approval_operation=approval_operation,
            order=order,
            kind=CommissionAdjustment.Kind.INITIAL,
            old_amount=Decimal("0.000"),
            new_amount=Decimal("876.543"),
            delta=Decimal("876.543"),
            confirmed_at=now,
        )

    @staticmethod
    def png_bytes():
        buffer = BytesIO()
        Image.new("RGB", (2, 2), "white").save(buffer, format="PNG")
        return buffer.getvalue()

    def upload_payment_image(self, client, filename="synthetic.png"):
        response = client.post("/api/v1/images", {
            "file": SimpleUploadedFile(filename, self.png_bytes(), content_type="image/png"),
        })
        self.assertEqual(response.status_code, 200, response.content)
        return int(response.json()["image_id"])

    def completed_onsite_order(self):
        _, _, order = self.preview_and_confirm(self.order_payload(), actor=self.sales_two)
        image = self.add_payment_image(owner=self.sales_two)
        order.refresh_from_db()
        register_payment(self.sales_two, order.pk, {
            "operation_key": str(uuid4()),
            "expected_version": order.version,
            "account_id": self.public_account.pk,
            "received_amount": f"{order.revenue_amount:.3f}",
            "image_ids": [image.pk],
        })
        order.refresh_from_db()
        deliver_on_site(self.user, order.pk, {
            "operation_key": str(uuid4()),
            "expected_version": order.version,
            "outbound_freights": [],
        })
        order.refresh_from_db()
        self.verify(order, actor=self.user, result="passed")
        order.refresh_from_db()
        return order

    def test_view_and_change_all_order_permissions_are_independent_over_http(self):
        self.balance.on_hand = 100
        self.balance.cost_amount = Decimal("100.000")
        self.balance.save(update_fields=("on_hand", "cost_amount"))
        cases = (
            (False, False),
            (True, False),
            (False, True),
            (True, True),
        )
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            for index, (can_view_all, can_change_all) in enumerate(cases):
                with self.subTest(view_all=can_view_all, change_all=can_change_all):
                    _, _, order = self.preview_and_confirm(self.order_payload(), actor=self.sales_two)
                    self.set_commission_marker(order)
                    actor = self.new_sales_user(f"scope-sales-{index}")
                    if can_view_all:
                        self.grant_permission(actor, "view_all_sales_orders")
                    if can_change_all:
                        self.grant_permission(actor, "change_all_sales_orders")
                    client = Client()
                    client.force_login(actor)

                    detail = client.get(f"/api/v1/orders/{order.pk}")
                    self.assertEqual(detail.status_code, 200 if can_view_all else 404)
                    if can_view_all:
                        detail_data = detail.json()
                        for field in ("commission_estimate", "commission_review", "commission_adjustments"):
                            self.assertNotIn(field, detail_data)
                        listing = client.get("/api/v1/orders").json()
                        self.assertIn(order.pk, [item["id"] for item in listing["items"]])
                        for item in listing["items"]:
                            for field in ("commission_estimate", "commission_review", "commission_adjustments"):
                                self.assertNotIn(field, item)
                        page = client.get(f"/orders/{order.pk}/")
                        self.assertNotContains(page, "提成预估")
                        self.assertNotContains(page, "987.654")
                        self.assertNotContains(page, "876.543")
                        if can_change_all:
                            self.assertContains(page, "修改订单")
                            self.assertContains(page, "取消订单并释放占货")
                            self.assertContains(page, "登记或更新收款")
                        else:
                            self.assertNotContains(page, "修改订单")
                            self.assertNotContains(page, "取消订单并释放占货")
                            self.assertNotContains(page, "登记或更新收款")

                    if can_change_all:
                        edit_page = client.get(f"/orders/{order.pk}/edit/")
                        self.assertEqual(edit_page.status_code, 200)
                        self.assertNotContains(edit_page, '"commission_estimate"')
                        self.assertNotContains(edit_page, "987.654")
                        self.assertNotContains(edit_page, "876.543")
                        amendment = client.post(
                            f"/api/v1/orders/{order.pk}/amend/preview",
                            data=json.dumps(self.amendment_payload(order)),
                            content_type="application/json",
                        )
                        self.assertEqual(amendment.status_code, 200, amendment.content)
                        self.assertNotIn("commission_estimate", amendment.json())

                    order.refresh_from_db()
                    original_version = order.version
                    original_order_sequence = order.last_sequence
                    original_global_sequence = BusinessControl.objects.get(pk=1).last_sequence
                    original_payment_operations = BusinessOperation.objects.filter(
                        sales_order=order, action="payment_register"
                    ).count()
                    image_id = self.upload_payment_image(client, f"synthetic-{index}.png")
                    payload = {
                        "operation_key": str(uuid4()),
                        "expected_version": original_version,
                        "account_id": self.public_account.pk,
                        "received_amount": f"{order.revenue_amount:.3f}",
                        "image_ids": [image_id],
                    }
                    payment_response = client.post(
                        f"/api/v1/orders/{order.pk}/payment",
                        data=json.dumps(payload),
                        content_type="application/json",
                    )
                    self.assertEqual(payment_response.status_code, 200 if can_change_all else 404, payment_response.content)
                    order.refresh_from_db()
                    image = ReceiptImage.objects.get(pk=image_id)
                    if can_change_all:
                        self.assertIsNotNone(image.payment_id)
                        self.assertEqual(image.payment.order_id, order.pk)
                        self.assertEqual(order.version, original_version + 1)
                        self.assertGreater(order.last_sequence, original_order_sequence)
                        self.assertGreater(BusinessControl.objects.get(pk=1).last_sequence, original_global_sequence)
                        self.assertEqual(
                            BusinessOperation.objects.filter(sales_order=order, action="payment_register").count(),
                            original_payment_operations + 1,
                        )
                    else:
                        self.assertFalse(PaymentRecord.objects.filter(order=order).exists())
                        self.assertIsNone(image.payment_id)
                        self.assertEqual(order.version, original_version)
                        self.assertEqual(order.last_sequence, original_order_sequence)
                        self.assertEqual(BusinessControl.objects.get(pk=1).last_sequence, original_global_sequence)
                        self.assertEqual(
                            BusinessOperation.objects.filter(sales_order=order, action="payment_register").count(),
                            original_payment_operations,
                        )

    def grant_permission(self, user, codename):
        permission = Permission.objects.get(content_type__app_label="crm", codename=codename)
        user.user_permissions.add(permission)

    def test_change_all_without_register_permission_cannot_create_payment(self):
        _, _, order = self.preview_and_confirm(self.order_payload(), actor=self.sales_two)
        actor = User.objects.create_user(username="change-only-user", password="test-only-password")
        self.grant_permission(actor, "view_salesorder")
        self.grant_permission(actor, "change_all_sales_orders")
        image = self.add_payment_image(owner=actor)
        order.refresh_from_db()
        sequence = BusinessControl.objects.get(pk=1).last_sequence
        client = Client()
        client.force_login(actor)
        response = client.post(
            f"/api/v1/orders/{order.pk}/payment",
            data=json.dumps({
                "operation_key": str(uuid4()),
                "expected_version": order.version,
                "account_id": self.public_account.pk,
                "received_amount": f"{order.revenue_amount:.3f}",
                "image_ids": [image.pk],
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(PaymentRecord.objects.filter(order=order).exists())
        self.assertIsNone(ReceiptImage.objects.get(pk=image.pk).payment_id)
        self.assertEqual(BusinessControl.objects.get(pk=1).last_sequence, sequence)

    def test_lost_change_all_scope_blocks_exact_payment_replay_on_next_request(self):
        _, _, order = self.preview_and_confirm(self.order_payload(), actor=self.sales_two)
        actor = self.new_sales_user("scope-revoked-sales", "view_all_sales_orders", "change_all_sales_orders")
        client = Client()
        client.force_login(actor)
        image_id = self.upload_payment_image(client)
        order.refresh_from_db()
        payload = {
            "operation_key": str(uuid4()),
            "expected_version": order.version,
            "account_id": self.public_account.pk,
            "received_amount": f"{order.revenue_amount:.3f}",
            "image_ids": [image_id],
        }
        url = f"/api/v1/orders/{order.pk}/payment"
        first = client.post(url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(first.status_code, 200, first.content)
        order.refresh_from_db()
        payment = PaymentRecord.objects.get(order=order)
        sequence = BusinessControl.objects.get(pk=1).last_sequence
        self.assertEqual(client.get(f"/api/v1/orders/{order.pk}").status_code, 200)

        actor.user_permissions.remove(Permission.objects.get(content_type__app_label="crm", codename="change_all_sales_orders"))
        replay = client.post(url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(replay.status_code, 404)
        order.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentRecord.Status.PENDING)
        self.assertEqual(order.version, payload["expected_version"] + 1)
        self.assertEqual(BusinessControl.objects.get(pk=1).last_sequence, sequence)
        self.assertEqual(ReceiptImage.objects.filter(payment=payment).count(), 1)

    def test_commission_fields_are_visible_to_owner_commission_viewers_and_admin_only(self):
        _, _, order = self.preview_and_confirm(self.order_payload(), actor=self.sales_two)
        self.set_commission_marker(order)
        all_commissions = self.new_sales_user("commission-viewer", "view_all_sales_orders", "view_all_commissions")
        administrator = User.objects.create_superuser(username="phase4-admin", password="test-only-password")

        for actor in (self.sales_two, all_commissions, self.owner, administrator):
            with self.subTest(actor=actor.username):
                client = Client()
                client.force_login(actor)
                detail = client.get(f"/api/v1/orders/{order.pk}")
                self.assertEqual(detail.status_code, 200, detail.content)
                data = detail.json()
                self.assertEqual(data["commission_estimate"], "987.654")
                self.assertEqual(data["commission_review"]["approved_amount"], "876.543")
                self.assertEqual(data["commission_adjustments"][0]["delta"], "876.543")
                self.assertContains(client.get(f"/orders/{order.pk}/"), "987.654")

    def test_commission_review_response_is_masked_without_commission_view(self):
        order = self.completed_onsite_order()
        reviewer = self.new_sales_user("limited-commission-reviewer", "view_all_sales_orders", "review_commission")
        client = Client()
        client.force_login(reviewer)
        payload = {"operation_key": str(uuid4()), "expected_version": order.version}
        response = client.post(
            f"/api/v1/orders/{order.pk}/review-commission",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        result = response.json()
        for field in ("approved_amount", "first_earned_at", "adjustment_id", "delta"):
            self.assertNotIn(field, result)
        operation = BusinessOperation.objects.get(sales_order=order, action="commission_review")
        for field in ("approved_amount", "first_earned_at", "adjustment_id", "delta"):
            self.assertNotIn(field, operation.result)
        hidden_detail = client.get(f"/api/v1/orders/{order.pk}").json()
        for field in ("commission_estimate", "commission_review", "commission_adjustments"):
            self.assertNotIn(field, hidden_detail)

    def test_commission_review_replay_masks_fields_after_permission_revocation(self):
        order = self.completed_onsite_order()
        reviewer = self.new_sales_user(
            "revoked-commission-reviewer",
            "view_all_sales_orders",
            "view_all_commissions",
            "review_commission",
        )
        client = Client()
        client.force_login(reviewer)
        payload = {"operation_key": str(uuid4()), "expected_version": order.version}
        url = f"/api/v1/orders/{order.pk}/review-commission"
        first = client.post(url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(first.status_code, 200, first.content)
        result_fields = {"approved_amount", "first_earned_at", "adjustment_id", "delta"}
        self.assertTrue(result_fields.issubset(first.json()))

        reviewer.user_permissions.remove(
            Permission.objects.get(content_type__app_label="crm", codename="view_all_commissions")
        )
        replay = client.post(url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertTrue(result_fields.isdisjoint(replay.json()))
        stored_operation = BusinessOperation.objects.get(sales_order=order, action="commission_review")
        self.assertTrue(result_fields.issubset(stored_operation.result))

    def test_order_detail_hides_other_sales_actions_and_preserves_warehouse_and_owner_actions(self):
        payload = self.order_payload(
            delivery_mode=SalesOrder.DeliveryMode.SHIPMENT,
            address_snapshot={
                "recipient": "Synthetic Recipient",
                "contact": "Synthetic Contact",
                "province": "北京市",
                "city": "北京市",
                "detail": "Synthetic street 1",
            },
        )
        _, _, order = self.preview_and_confirm(payload, actor=self.sales_two)
        image = self.add_payment_image(owner=self.sales_two)
        order.refresh_from_db()
        register_payment(self.sales_two, order.pk, {
            "operation_key": str(uuid4()),
            "expected_version": order.version,
            "account_id": self.public_account.pk,
            "received_amount": f"{order.revenue_amount:.3f}",
            "image_ids": [image.pk],
        })
        order.refresh_from_db()
        ship_sales_order(self.user, order.pk, self.shipment_data(order))
        order.refresh_from_db()
        self.set_commission_marker(order)

        viewer = self.new_sales_user("other-sales-order-viewer", "view_all_sales_orders")
        viewer_client = Client()
        viewer_client.force_login(viewer)
        response = viewer_client.get(f"/orders/{order.pk}/")
        self.assertEqual(response.status_code, 200)
        for label in (
            "提成预估",
            "正式核定提成",
            "提成复核",
            "提成差额（线下处理登记）",
            "登记或更新收款",
            "确认收货",
        ):
            self.assertNotContains(response, label)
        self.assertNotContains(response, "987.654")
        self.assertNotContains(response, "876.543")

        warehouse_client = Client()
        warehouse_client.force_login(self.user)
        warehouse_page = warehouse_client.get(f"/orders/{order.pk}/")
        self.assertContains(warehouse_page, "收款核对")
        self.assertContains(warehouse_page, "补录或调整出库运费")
        self.assertContains(warehouse_page, "提成预估")

        seller_client = Client()
        seller_client.force_login(self.sales_two)
        seller_page = seller_client.get(f"/orders/{order.pk}/")
        self.assertContains(seller_page, "确认收货")
        self.assertContains(seller_page, "987.654")

        order.refresh_from_db()
        self.verify(order, actor=self.user, result="passed")
        order.refresh_from_db()
        receive_sales_order(self.sales_two, order.pk, {
            "operation_key": str(uuid4()),
            "expected_version": order.version,
            "outbound_freights": [],
        })
        order.refresh_from_db()
        update_outbound_freight(self.user, order.pk, {
            "operation_key": str(uuid4()),
            "expected_version": order.version,
            "outbound_freights": [{
                "line_id": order.lines.filter(active=True).first().pk,
                "amount": "1.000",
            }],
        })
        order.refresh_from_db()
        owner_client = Client()
        owner_client.force_login(self.owner)
        self.assertContains(owner_client.get(f"/orders/{order.pk}/"), "复核当前提成")
        review_commission(self.owner, order.pk, {
            "operation_key": str(uuid4()),
            "expected_version": order.version,
        })
        order.refresh_from_db()
        seller_page = seller_client.get(f"/orders/{order.pk}/")
        self.assertContains(seller_page, "标记线下处理完成")


class OrderListStatusTests(Phase4Base):
    def test_model_status_labels_and_each_status_filter_preserve_order_scope(self):
        self.balance.on_hand = 100
        self.balance.cost_amount = Decimal("100.000")
        self.balance.save(update_fields=("on_hand", "cost_amount"))

        awaiting = self.new_order()
        ready = self.new_order()
        self.register(ready)
        ready.refresh_from_db()

        shipped = self.new_order(delivery_mode=SalesOrder.DeliveryMode.SHIPMENT)
        self.register(shipped)
        shipped.refresh_from_db()
        ship_sales_order(self.user, shipped.pk, self.shipment_data(shipped))
        shipped.refresh_from_db()

        completed = self.new_order()
        self.register(completed)
        completed.refresh_from_db()
        deliver_on_site(self.user, completed.pk, {
            "operation_key": str(uuid4()),
            "expected_version": completed.version,
            "outbound_freights": [],
        })
        completed.refresh_from_db()

        cancelled = self.new_order()
        cancel_sales_order(self.sales, cancelled.pk, {
            "operation_key": str(uuid4()),
            "expected_version": cancelled.version,
        })
        cancelled.refresh_from_db()
        other_awaiting = self.preview_and_confirm(self.order_payload(), actor=self.sales_two)[2]

        orders_by_status = {
            SalesOrder.Status.AWAITING_PAYMENT: {awaiting.pk, other_awaiting.pk},
            SalesOrder.Status.READY_TO_SHIP: {ready.pk},
            SalesOrder.Status.SHIPPED: {shipped.pk},
            SalesOrder.Status.COMPLETED: {completed.pk},
            SalesOrder.Status.CANCELLED: {cancelled.pk},
        }
        expected_labels = {
            order.pk: order.get_status_display()
            for order in (awaiting, ready, shipped, completed, cancelled, other_awaiting)
        }

        viewer = self.User.objects.create_user(username="all-orders-viewer", password="test-only-password")
        viewer.groups.add(Group.objects.get(name=ROLE_SALES))
        viewer.user_permissions.add(
            Permission.objects.get(content_type__app_label="crm", codename="view_all_sales_orders")
        )
        viewer_client = Client()
        viewer_client.force_login(viewer)
        listing = viewer_client.get("/api/v1/orders").json()
        listed_by_id = {item["id"]: item for item in listing["items"]}
        self.assertEqual(set(listed_by_id), set(expected_labels))
        for order_id, label in expected_labels.items():
            self.assertEqual(listed_by_id[order_id]["status_label"], label)
            self.assertNotIn("commission_estimate", listed_by_id[order_id])

        for status, expected_ids in orders_by_status.items():
            with self.subTest(status=status):
                filtered = viewer_client.get("/api/v1/orders", {"status": status})
                self.assertEqual(filtered.status_code, 200, filtered.content)
                data = filtered.json()
                self.assertEqual(data["total"], len(expected_ids))
                self.assertEqual({item["id"] for item in data["items"]}, expected_ids)
                self.assertTrue(all(item["status"] == status for item in data["items"]))

        own_client = Client()
        own_client.force_login(self.sales)
        own_listing = own_client.get("/api/v1/orders").json()
        self.assertEqual({item["id"] for item in own_listing["items"]}, {
            awaiting.pk, ready.pk, shipped.pk, completed.pk, cancelled.pk,
        })

        page = viewer_client.get("/orders/")
        self.assertEqual(list(page.context["status_options"]), list(SalesOrder.Status.choices))
        for status, label in SalesOrder.Status.choices:
            self.assertContains(page, f'<option value="{status}">{label}</option>')


class OrderListFilterTests(Phase4Base):
    def setUp(self):
        super().setUp()
        self.balance.on_hand = 100
        self.balance.cost_amount = Decimal("100.000")
        self.balance.save(update_fields=("on_hand", "cost_amount"))

    def test_home_is_login_only_and_renders_only_currently_authorized_data_blocks(self):
        anonymous = Client().get("/")
        self.assertEqual(anonymous.status_code, 302)

        viewer = self.User.objects.create_user(username="workbench-permission-viewer", password="test-only-password")
        permission = Permission.objects.get(content_type__app_label="crm", codename="view_salesorder")
        viewer.user_permissions.add(permission)
        client = Client()
        client.force_login(viewer)
        authorized = client.get("/")
        self.assertEqual(authorized.status_code, 200, authorized.content)
        self.assertContains(authorized, "今日已交付销售额")
        self.assertContains(authorized, "/api/v1/orders")
        self.assertEqual(client.get("/api/v1/orders").status_code, 200)

        viewer.user_permissions.clear()
        client = Client()
        client.force_login(self.User.objects.get(pk=viewer.pk))
        shell = client.get("/")
        self.assertEqual(shell.status_code, 200, shell.content)
        self.assertContains(shell, "工作台")
        self.assertNotContains(shell, "今日已交付销售额")
        self.assertNotContains(shell, "/api/v1/orders")
        self.assertEqual(client.get("/api/v1/orders").status_code, 403)

    def test_order_date_filter_uses_inclusive_beijing_created_at_bounds(self):
        shanghai = ZoneInfo("Asia/Shanghai")
        before = self.new_order()
        start = self.new_order()
        end = self.new_order()
        after = self.new_order()
        for order, created_at in (
            (before, datetime(2025, 10, 1, 23, 59, 59, 999999, tzinfo=shanghai)),
            (start, datetime(2025, 10, 2, 0, 0, tzinfo=shanghai)),
            (end, datetime(2025, 10, 3, 23, 59, 59, 999999, tzinfo=shanghai)),
            (after, datetime(2025, 10, 4, 0, 0, tzinfo=shanghai)),
        ):
            SalesOrder.objects.filter(pk=order.pk).update(created_at=created_at)

        client = Client()
        client.force_login(self.sales)
        first_page = client.get("/api/v1/orders", {
            "start_date": "2025-10-02",
            "end_date": "2025-10-03",
            "page": 1,
            "page_size": 1,
        })
        self.assertEqual(first_page.status_code, 200, first_page.content)
        self.assertEqual(first_page.json()["total"], 2)
        second_page = client.get("/api/v1/orders", {
            "start_date": "2025-10-02",
            "end_date": "2025-10-03",
            "page": 2,
            "page_size": 1,
        })
        self.assertEqual(second_page.status_code, 200, second_page.content)
        ids = [item["id"] for page in (first_page.json(), second_page.json()) for item in page["items"]]
        self.assertEqual(set(ids), {start.pk, end.pk})

        incomplete = client.get("/api/v1/orders", {"start_date": "2025-10-02"})
        self.assertEqual(incomplete.status_code, 400)
        self.assertIn("end_date", incomplete.json()["error"]["fields"])
        reversed_dates = client.get("/api/v1/orders", {
            "start_date": "2025-10-03",
            "end_date": "2025-10-02",
        })
        self.assertEqual(reversed_dates.status_code, 400)
        self.assertIn("start_date", reversed_dates.json()["error"]["fields"])
        overflow_date = client.get("/api/v1/orders", {
            "start_date": "9999-12-31",
            "end_date": "9999-12-31",
        })
        self.assertEqual(overflow_date.status_code, 400)
        self.assertIn("end_date", overflow_date.json()["error"]["fields"])

    def test_order_number_salesperson_and_payment_status_filters(self):
        pending = self.new_order()
        self.register(pending)
        problem = self.new_order()
        self.register(problem)
        self.verify(problem, result=PaymentRecord.Status.PROBLEM, problem_reason="Synthetic mismatch")
        passed = self.new_order()
        self.register(passed)
        self.verify(passed, result=PaymentRecord.Status.PASSED)
        no_evidence = self.new_order()
        other_salesperson = self.preview_and_confirm(self.order_payload(), actor=self.sales_two)[2]

        viewer = self.sales
        viewer.user_permissions.add(
            Permission.objects.get(content_type__app_label="crm", codename="view_all_sales_orders")
        )
        client = Client()
        client.force_login(viewer)

        for status, expected in (
            (PaymentRecord.Status.PENDING, pending),
            (PaymentRecord.Status.PROBLEM, problem),
            (PaymentRecord.Status.PASSED, passed),
        ):
            with self.subTest(verification_status=status):
                response = client.get("/api/v1/orders", {"verification_status": status})
                self.assertEqual(response.status_code, 200, response.content)
                data = response.json()
                self.assertEqual(data["total"], 1)
                self.assertEqual(data["items"][0]["id"], expected.pk)
        pending_ids = {
            item["id"]
            for item in client.get("/api/v1/orders", {"verification_status": PaymentRecord.Status.PENDING}).json()["items"]
        }
        self.assertNotIn(no_evidence.pk, pending_ids)

        salesperson = client.get("/api/v1/orders", {"salesperson_id": self.sales_two.pk}).json()
        self.assertEqual(salesperson["total"], 1)
        self.assertEqual(salesperson["items"][0]["id"], other_salesperson.pk)

        pending.refresh_from_db()
        for query, expected in ((str(pending.last_sequence), pending), (str(no_evidence.pk), no_evidence)):
            response = client.get("/api/v1/orders", {"q": query})
            self.assertEqual(response.status_code, 200, response.content)
            self.assertIn(expected.pk, {item["id"] for item in response.json()["items"]})

        invalid_status = client.get("/api/v1/orders", {"verification_status": "none"})
        self.assertEqual(invalid_status.status_code, 400)
        self.assertIn("verification_status", invalid_status.json()["error"]["fields"])
        invalid_id = client.get("/api/v1/orders", {"warehouse_id": "abc"})
        self.assertEqual(invalid_id.status_code, 400)
        self.assertIn("warehouse_id", invalid_id.json()["error"]["fields"])

    def test_customer_filter_uses_exact_id_and_enforces_both_visibility_scopes(self):
        first_customer = self.create_customer(self.sales, name="Synthetic Same Name")
        second_customer = self.create_customer(self.sales, name="Synthetic Same Name")
        first_order = self.preview_and_confirm(self.order_payload(customer_id=first_customer.pk))[2]
        second_order = self.preview_and_confirm(self.order_payload(customer_id=second_customer.pk))[2]
        client = Client()
        client.force_login(self.sales)

        for customer, expected in ((first_customer, first_order), (second_customer, second_order)):
            response = client.get("/api/v1/orders", {"customer_id": customer.pk})
            self.assertEqual(response.status_code, 200, response.content)
            data = response.json()
            self.assertEqual(data["total"], 1)
            self.assertEqual(data["items"][0]["id"], expected.pk)

        Customer.objects.filter(pk=second_customer.pk).update(owner=self.sales_two)
        hidden_customer = client.get("/api/v1/orders", {"customer_id": second_customer.pk})
        self.assertEqual(hidden_customer.status_code, 404)
        self.assertEqual(hidden_customer.json()["error"]["message"], "客户不存在或不在可见范围。")

        second_client = Client()
        second_client.force_login(self.sales_two)
        visible_customer_orders = second_client.get("/api/v1/orders", {"customer_id": second_customer.pk})
        self.assertEqual(visible_customer_orders.status_code, 200, visible_customer_orders.content)
        self.assertEqual(visible_customer_orders.json()["total"], 0)

    def test_warehouse_filter_uses_active_lines_and_paginates_unique_orders(self):
        second_sku = self.new_sku(2)
        self.add_balance(self.sku, self.second_warehouse, 20, "1.000")
        self.add_balance(second_sku, self.warehouse, 20, "1.000")
        self.add_balance(second_sku, self.second_warehouse, 20, "1.000")
        multi_warehouse = self.new_order(lines=[
            {"sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk, "quantity": 1, "sale_price": "5.000"},
            {"sku_id": self.sku.pk, "warehouse_id": self.second_warehouse.pk, "quantity": 1, "sale_price": "5.000"},
            {"sku_id": second_sku.pk, "warehouse_id": self.second_warehouse.pk, "quantity": 1, "sale_price": "5.000"},
        ])
        second_order = self.new_order(lines=[
            {"sku_id": self.sku.pk, "warehouse_id": self.second_warehouse.pk, "quantity": 1, "sale_price": "5.000"},
        ])
        amended = self.new_order(lines=[
            {"sku_id": second_sku.pk, "warehouse_id": self.second_warehouse.pk, "quantity": 1, "sale_price": "5.000"},
        ])
        old_line = amended.lines.get(active=True)
        amendment = self.amendment_payload(amended)
        amendment["lines"][0]["warehouse_id"] = self.warehouse.pk
        self.amend(amended, amendment)
        self.assertFalse(amended.lines.filter(pk=old_line.pk, active=True).exists())

        client = Client()
        client.force_login(self.sales)
        first_page = client.get("/api/v1/orders", {
            "warehouse_id": self.second_warehouse.pk,
            "page": 1,
            "page_size": 1,
        })
        self.assertEqual(first_page.status_code, 200, first_page.content)
        self.assertEqual(first_page.json()["total"], 2)
        second_page = client.get("/api/v1/orders", {
            "warehouse_id": self.second_warehouse.pk,
            "page": 2,
            "page_size": 1,
        })
        self.assertEqual(second_page.status_code, 200, second_page.content)
        ids = [item["id"] for page in (first_page.json(), second_page.json()) for item in page["items"]]
        self.assertEqual(set(ids), {multi_warehouse.pk, second_order.pk})
        self.assertEqual(len(ids), len(set(ids)))

    def test_order_filter_options_cover_visible_orders_before_filters_and_pagination(self):
        own_order = self.new_order()
        self.add_balance(self.sku, self.second_warehouse, 20, "1.000")
        other_order = self.preview_and_confirm(self.order_payload(lines=[{
            "sku_id": self.sku.pk,
            "warehouse_id": self.second_warehouse.pk,
            "quantity": 1,
            "sale_price": "5.000",
        }]), actor=self.sales_two)[2]
        client = Client()
        client.force_login(self.sales)

        own_scope = client.get("/api/v1/orders", {"page_size": 1}).json()
        self.assertEqual(own_scope["total"], 1)
        self.assertEqual(own_scope["items"][0]["id"], own_order.pk)
        self.assertEqual(
            own_scope["filter_options"],
            {
                "salespeople": [{"id": str(self.sales.pk), "label": self.sales.get_username()}],
                "warehouses": [{"id": str(self.warehouse.pk), "label": self.warehouse.name}],
            },
        )
        hidden = client.get("/api/v1/orders", {"salesperson_id": self.sales_two.pk, "page_size": 1}).json()
        self.assertEqual(hidden["total"], 0)
        self.assertEqual(len(hidden["filter_options"]["salespeople"]), 1)
        self.assertEqual(len(hidden["filter_options"]["warehouses"]), 1)

        self.sales.user_permissions.add(
            Permission.objects.get(content_type__app_label="crm", codename="view_all_sales_orders")
        )
        all_pages = client.get("/api/v1/orders", {"page_size": 1}).json()
        self.assertEqual(all_pages["total"], 2)
        self.assertEqual(len(all_pages["items"]), 1)
        self.assertEqual(len(all_pages["filter_options"]["salespeople"]), 2)
        self.assertEqual(len(all_pages["filter_options"]["warehouses"]), 2)
        all_scope = client.get("/api/v1/orders", {
            "salesperson_id": self.sales_two.pk,
            "page_size": 1,
        }).json()
        self.assertEqual(all_scope["total"], 1)
        self.assertEqual(all_scope["items"][0]["id"], other_order.pk)
        self.assertEqual(
            all_scope["filter_options"]["salespeople"],
            [
                {"id": str(self.sales.pk), "label": self.sales.get_username()},
                {"id": str(self.sales_two.pk), "label": self.sales_two.get_username()},
            ],
        )
        self.assertEqual(
            all_scope["filter_options"]["warehouses"],
            [
                {"id": str(self.warehouse.pk), "label": self.warehouse.name},
                {"id": str(self.second_warehouse.pk), "label": self.second_warehouse.name},
            ],
        )
