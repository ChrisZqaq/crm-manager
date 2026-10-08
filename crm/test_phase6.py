from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.contrib.auth.models import Permission
from django.test import SimpleTestCase
from django.utils import timezone

from .models import (
    BusinessControl,
    BusinessOperation,
    CommissionAdjustment,
    CommissionReview,
    InventoryBalance,
    InventoryHold,
    PaymentRecord,
    ReceiptImage,
    SalesOrder,
    Warehouse,
)
from .operations import OperationError
from .reports import _date_criteria, build_report, workbook_bytes
from .test_phase4 import Phase4Base
from .fulfillment import deliver_on_site, register_payment, ship_sales_order


SHANGHAI = ZoneInfo("Asia/Shanghai")


class ReportDateCriteriaTests(SimpleTestCase):
    def test_rolling_month_clamps_month_end_and_keeps_beijing_bounds(self):
        result = _date_criteria({"period": "6m"}, today=date(2026, 8, 31))
        self.assertEqual(result["start_date"], "2026-02-28")
        self.assertEqual(result["end_date"], "2026-08-31")
        self.assertEqual(result["start_at"], datetime(2026, 2, 28, tzinfo=SHANGHAI))
        self.assertEqual(result["end_before"], datetime(2026, 9, 1, tzinfo=SHANGHAI))

    def test_leap_day_and_custom_range_validation(self):
        leap = _date_criteria({"period": "6m"}, today=date(2024, 8, 31))
        self.assertEqual(leap["start_date"], "2024-02-29")
        custom = _date_criteria({"period": "custom", "start_date": "2026-02-28", "end_date": "2026-03-01"})
        self.assertEqual(custom["start_date"], "2026-02-28")
        with self.assertRaises(OperationError):
            _date_criteria({"period": "custom", "start_date": "2026-03-02", "end_date": "2026-03-01"})
        with self.assertRaises(OperationError):
            _date_criteria({"period": "custom", "start_date": "2026-02-30", "end_date": "2026-03-01"})


class ReportPhase6Tests(Phase4Base):
    def setUp(self):
        super().setUp()
        self.balance.on_hand = 20
        self.balance.cost_amount = Decimal("20.000")
        self.balance.save(update_fields=("on_hand", "cost_amount"))

    def _set_sales_date(self, order, field, value):
        SalesOrder.objects.filter(pk=order.pk).update(**{field: value})

    def test_sales_groups_order_lines_per_unit_warehouse_and_snapshot_region(self):
        second_balance = InventoryBalance.objects.create(
            sku=self.sku, warehouse=self.second_warehouse, on_hand=10, cost_amount=Decimal("20.000")
        )
        pack_sku = self.new_sku(2)
        pack_sku.unit = "包"
        pack_sku.save(update_fields=("unit",))
        InventoryBalance.objects.create(sku=pack_sku, warehouse=self.warehouse, on_hand=10, cost_amount=Decimal("20.000"))
        order = self.new_order(delivery_mode=SalesOrder.DeliveryMode.SHIPMENT, lines=[
            {"sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk, "quantity": 1, "sale_price": "5.000"},
            {"sku_id": self.sku.pk, "warehouse_id": self.warehouse.pk, "quantity": 1, "sale_price": "5.000"},
            {"sku_id": self.sku.pk, "warehouse_id": self.second_warehouse.pk, "quantity": 1, "sale_price": "5.000"},
            {"sku_id": pack_sku.pk, "warehouse_id": self.warehouse.pk, "quantity": 2, "sale_price": "5.000"},
        ])
        self.register(order)
        self.verify(order)
        order.refresh_from_db()
        ship_sales_order(self.user, order.pk, self.shipment_data(order))
        shanghai_delivery = datetime(2025, 10, 1, 15, 59, 59, 999999, tzinfo=ZoneInfo("UTC"))
        self._set_sales_date(order, "shipped_at", shanghai_delivery)
        self._set_sales_date(order, "province_snapshot", "浙江省")
        self._set_sales_date(order, "city_snapshot", "杭州市")

        data = build_report(self.sales, "sales", {
            "period": "custom", "start_date": "2025-10-01", "end_date": "2025-10-01",
        })
        self.assertEqual(len(data["items"]), 3)
        first = next(row for row in data["items"] if row["warehouse_id"] == str(self.warehouse.pk))
        second = next(row for row in data["items"] if row["warehouse_id"] == str(self.second_warehouse.pk))
        self.assertEqual(first["quantity"], 2)
        self.assertEqual(first["order_count"], 1)
        self.assertEqual(first["revenue_amount"], "10.000")
        self.assertEqual(first["cost_amount"], "2.000")
        self.assertEqual(first["margin_amount"], "8.000")
        self.assertEqual(second["quantity"], 1)
        self.assertEqual(second["cost_amount"], "2.000")
        self.assertEqual(second["province"], "浙江省")
        self.assertEqual(second["city"], "杭州市")
        self.assertEqual(data["summary"]["quantity_by_unit"], [{"unit": "包", "quantity": 2}, {"unit": "盒", "quantity": 3}])
        filtered = build_report(self.sales, "sales", {
            "period": "custom", "start_date": "2025-10-01", "end_date": "2025-10-01",
            "warehouse_id": str(self.warehouse.pk),
        })
        self.assertEqual(len(filtered["items"]), 2)
        outside = build_report(self.sales, "sales", {
            "period": "custom", "start_date": "2025-10-02", "end_date": "2025-10-02",
        })
        self.assertEqual(outside["items"], [])

        daily_query = {
            "period": "custom",
            "start_date": "2025-09-30",
            "end_date": "2025-10-02",
            "series": "day",
        }
        daily = build_report(self.sales, "sales", daily_query)
        self.assertEqual(daily["series"], [
            {"date": "2025-09-30", "revenue_amount": "0.000"},
            {"date": "2025-10-01", "revenue_amount": "25.000"},
            {"date": "2025-10-02", "revenue_amount": "0.000"},
        ])
        self.assertEqual(
            sum((Decimal(point["revenue_amount"]) for point in daily["series"]), Decimal("0.000")),
            Decimal(daily["summary"]["totals"]["revenue_amount"]),
        )
        self.client.force_login(self.sales)
        series_response = self.client.get("/api/v1/reports/sales", daily_query)
        self.assertEqual(series_response.status_code, 200, series_response.content)
        self.assertEqual(series_response.json()["series"], daily["series"])
        too_many_days = self.client.get("/api/v1/reports/sales", {
            "period": "custom",
            "start_date": "2024-01-01",
            "end_date": "2025-01-01",
            "series": "day",
        })
        self.assertEqual(too_many_days.status_code, 400)
        self.assertIn("end_date", too_many_days.json()["error"]["fields"])
        page = self.client.get("/reports/sales/?period=custom&start_date=2025-10-01&end_date=2025-10-01")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "杭州市")
        self.assertContains(page, "毛利")
        second_balance.refresh_from_db()
        self.assertEqual(second_balance.on_hand, 9)

    def test_unshipped_orders_are_explicit_and_never_mixed_into_sales(self):
        self.new_order()
        delivered = build_report(self.sales, "sales", {"period": "all"})
        pending = build_report(self.sales, "sales", {"scope": "unshipped", "period": "all"})
        self.assertEqual(delivered["items"], [])
        self.assertEqual(pending["summary"]["scope"], "unshipped")
        self.assertEqual(pending["items"][0]["row_type"], "未交付订单")
        with self.assertRaises(OperationError):
            build_report(self.sales, "sales", {"scope": "unshipped", "period": "1m"})

    def test_inventory_report_keeps_live_balance_and_hoard_reason(self):
        self.balance.hoarded = 2
        self.balance.save(update_fields=("hoarded",))
        InventoryHold.objects.create(
            balance=self.balance, type=InventoryHold.Type.HOARD, initial_quantity=2, remaining_quantity=2,
            reason="Synthetic event stock", created_by=self.user,
        )
        second_balance = InventoryBalance.objects.create(
            sku=self.sku, warehouse=self.second_warehouse, on_hand=5, cost_amount=Decimal("5.000")
        )
        pack_sku = self.new_sku(2)
        pack_sku.unit = "包"
        pack_sku.save(update_fields=("unit",))
        InventoryBalance.objects.create(sku=pack_sku, warehouse=self.warehouse, on_hand=4, cost_amount=Decimal("4.000"))
        data = build_report(self.sales, "inventory", {})
        row = next(item for item in data["items"] if item["id"] == str(self.balance.pk))
        self.assertEqual(row["available"], 18)
        self.assertIn("囤货（Synthetic event stock） 2 盒", row["lock_reasons"])
        self.assertEqual(data["summary"]["by_unit"], [
            {"unit": "包", "on_hand": 4, "available": 4, "occupied": 0, "reserved": 0, "damaged": 0},
            {"unit": "盒", "on_hand": 25, "available": 23, "occupied": 0, "reserved": 0, "damaged": 0},
        ])

        control = BusinessControl.objects.get(pk=1)
        control.low_stock_enabled = True
        control.default_threshold = 5
        control.save(update_fields=("low_stock_enabled", "default_threshold"))
        low_stock = build_report(self.sales, "inventory", {"low_stock": "true"})
        self.assertEqual(low_stock["summary"]["by_unit"], [
            {"unit": "包", "on_hand": 4, "available": 4, "occupied": 0, "reserved": 0, "damaged": 0},
            {"unit": "盒", "on_hand": 5, "available": 5, "occupied": 0, "reserved": 0, "damaged": 0},
        ])
        pack_sku.low_stock_enabled = False
        pack_sku.save(update_fields=("low_stock_enabled",))
        sku_disabled = build_report(self.sales, "inventory", {"low_stock": "true"})
        self.assertEqual(sku_disabled["summary"]["by_unit"], [
            {"unit": "盒", "on_hand": 5, "available": 5, "occupied": 0, "reserved": 0, "damaged": 0},
        ])
        control.low_stock_enabled = False
        control.save(update_fields=("low_stock_enabled",))
        global_disabled = build_report(self.sales, "inventory", {"low_stock": "true"})
        self.assertEqual(global_disabled["items"], [])
        self.assertEqual(global_disabled["summary"]["by_unit"], [])
        self.assertFalse(data["metadata"]["date_filter_available"])
        self.client.force_login(self.sales)
        page = self.client.get("/reports/inventory/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Synthetic event stock")

    def test_payment_first_evidence_time_survives_failure_replay_updates_and_rechecks(self):
        order = self.new_order()
        image = self.add_payment_image()
        request = {
            "operation_key": str(uuid4()),
            "expected_version": order.version,
            "account_id": self.public_account.pk,
            "received_amount": str(order.revenue_amount),
            "image_ids": [image.pk],
        }
        before = timezone.now()
        with patch("crm.fulfillment._create_operation", side_effect=RuntimeError("synthetic rollback")):
            with self.assertRaises(RuntimeError):
                register_payment(self.sales, order.pk, request)
        self.assertFalse(PaymentRecord.objects.filter(order=order).exists())
        self.assertIsNone(ReceiptImage.objects.get(pk=image.pk).payment_id)
        self.assertFalse(BusinessOperation.objects.filter(sales_order=order, action="payment_register").exists())
        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.Status.AWAITING_PAYMENT)

        result = register_payment(self.sales, order.pk, request)
        self.assertEqual(register_payment(self.sales, order.pk, request), result)
        payment = PaymentRecord.objects.get(order=order)
        first_registered_at = payment.first_evidence_registered_at
        operation = BusinessOperation.objects.get(operation_key=request["operation_key"])
        self.assertEqual(first_registered_at, operation.created_at)
        self.assertGreaterEqual(first_registered_at, before)
        self.assertLessEqual(first_registered_at, timezone.now())

        self.verify(order, actor=self.owner)
        second_image = self.add_payment_image()
        self.register(
            order,
            account=self.private_account,
            received_amount="9.000",
            difference_reason="Synthetic correction",
            image=second_image,
        )
        payment.refresh_from_db()
        self.assertEqual(payment.first_evidence_registered_at, first_registered_at)
        self.verify(order, actor=self.owner)
        self.verify(order, actor=self.owner)
        payment.refresh_from_db()
        self.assertEqual(payment.first_evidence_registered_at, first_registered_at)

    def test_payment_report_uses_first_evidence_registration_time(self):
        order = self.new_order()
        self.register(order)
        data = build_report(self.sales, "payments", {})
        self.assertEqual(len(data["items"]), 1)
        self.assertTrue(data["metadata"]["date_filter_available"])
        self.assertFalse(data["metadata"]["payment_date_basis_pending"])
        self.assertEqual(data["metadata"]["date_basis"], "首次登记收款凭证时间（北京时间）")
        self.assertEqual(data["summary"], {})
        self.assertTrue(data["items"][0]["first_evidence_registered_at"])
        self.assertEqual(data["items"][0]["actual_received_at"], "")
        self.assertIn("非实收核实", dict((key, label) for key, label, _ in data["columns"]).get("registered_amount", ""))
        self.client.force_login(self.sales)
        response = self.client.get("/api/v1/reports/payments?period=all")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["date_filter_available"])
        self.assertFalse(response.json()["payment_date_basis_pending"])
        page = self.client.get("/reports/payments/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "首次成功登记收款凭证时间")
        self.assertContains(page, "开始日期")
        invalid_page = self.client.get("/reports/payments/?page_size=not-a-number")
        self.assertEqual(invalid_page.status_code, 200)
        self.assertContains(invalid_page, "page与page_size必须为整数")

    def test_payment_report_applies_beijing_boundaries_and_matches_api_page_and_xlsx(self):
        registration_times = [
            datetime(2026, 9, 29, 23, 59, 59, 999999, tzinfo=SHANGHAI),
            datetime(2026, 9, 30, 0, 0, tzinfo=SHANGHAI),
            datetime(2026, 10, 1, 0, 0, tzinfo=SHANGHAI),
            datetime(2026, 10, 1, 23, 59, 59, 999999, tzinfo=SHANGHAI),
            datetime(2026, 10, 2, 0, 0, tzinfo=SHANGHAI),
        ]
        orders = []
        for registered_at in registration_times:
            order = self.new_order()
            self.register(order)
            PaymentRecord.objects.filter(order=order).update(first_evidence_registered_at=registered_at)
            orders.append(order)

        fixed_now = datetime(2026, 10, 31, 12, 0, tzinfo=SHANGHAI)
        custom_query = {"period": "custom", "start_date": "2026-10-01", "end_date": "2026-10-01"}
        with patch("crm.reports._now_local", return_value=fixed_now):
            rolling = build_report(self.sales, "payments", {"period": "1m"})
            custom = build_report(self.sales, "payments", custom_query)

        self.assertEqual((rolling["metadata"]["start_date"], rolling["metadata"]["end_date"]), ("2026-09-30", "2026-10-31"))
        self.assertEqual({row["order_id"] for row in rolling["items"]}, {str(order.pk) for order in orders[1:]})
        self.assertEqual({row["order_id"] for row in custom["items"]}, {str(orders[2].pk), str(orders[3].pk)})

        self.client.force_login(self.sales)
        query = "period=custom&start_date=2026-10-01&end_date=2026-10-01"
        with patch("crm.reports._now_local", return_value=fixed_now):
            api = self.client.get(f"/api/v1/reports/payments?{query}&page_size=200")
            page = self.client.get(f"/reports/payments/?{query}&page=2&page_size=1")
            export = self.client.get(f"/api/v1/reports/payments/export?{query}&page=2&page_size=1")

        self.assertEqual(api.status_code, 200)
        self.assertEqual(api.json()["total"], 2)
        self.assertEqual({row["order_id"] for row in api.json()["items"]}, {str(orders[2].pk), str(orders[3].pk)})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["total"], 2)
        self.assertEqual(page.context["page"], 2)
        self.assertContains(page, "首次登记收款凭证时间")
        self.assertEqual(export.status_code, 200)
        from io import BytesIO
        from openpyxl import load_workbook

        sheet = load_workbook(BytesIO(export.content), data_only=False).active
        self.assertEqual(sheet.max_row, 8)
        headers = [sheet.cell(6, column).value for column in range(1, sheet.max_column + 1)]
        registered_at_column = headers.index("首次登记收款凭证时间") + 1
        exported_dates = [sheet.cell(row, registered_at_column).value for row in range(7, 9)]
        api_dates = [row["first_evidence_registered_at"] for row in api.json()["items"]]
        self.assertEqual(exported_dates, api_dates)
        self.assertEqual(export["X-Report-Start-Date"], "2026-10-01")
        self.assertEqual(export["X-Report-End-Date"], "2026-10-01")

    def test_commission_uses_original_earned_date_and_lists_deltas_separately(self):
        order = self.new_order()
        self.register(order)
        self.verify(order)
        order.refresh_from_db()
        deliver_on_site(self.user, order.pk, {"operation_key": "8271cae4-2a34-4a55-9c15-7e64c9d0ae2c", "expected_version": order.version})
        order.refresh_from_db()
        earned_at = datetime(2025, 9, 30, 16, 30, tzinfo=ZoneInfo("UTC"))
        CommissionReview.objects.create(
            order=order, approved_amount=Decimal("1.000"), approved_amount_version=order.amount_version,
            first_earned_at=earned_at, reviewer=self.owner, reviewed_at=earned_at,
        )
        operations = list(BusinessOperation.objects.filter(sales_order=order).order_by("sequence"))
        self.assertGreaterEqual(len(operations), 3)
        CommissionAdjustment.objects.create(
            approval_operation=operations[0], order=order, kind=CommissionAdjustment.Kind.INITIAL,
            old_amount=Decimal("0.000"), new_amount=Decimal("1.000"), delta=Decimal("1.000"), confirmed_at=earned_at,
        )
        CommissionAdjustment.objects.create(
            approval_operation=operations[1], order=order, kind=CommissionAdjustment.Kind.REREVIEW,
            old_amount=Decimal("1.000"), new_amount=Decimal("1.200"), delta=Decimal("0.200"),
            confirmed_at=datetime(2025, 10, 2, 1, 0, tzinfo=SHANGHAI), completed_by=self.sales,
            completed_at=datetime(2025, 10, 2, 2, 0, tzinfo=SHANGHAI),
        )
        CommissionAdjustment.objects.create(
            approval_operation=operations[2], order=order, kind=CommissionAdjustment.Kind.COST_CORRECTION,
            old_amount=Decimal("1.200"), new_amount=Decimal("0.800"), delta=Decimal("-0.400"),
            confirmed_at=datetime(2025, 10, 3, 1, 0, tzinfo=SHANGHAI),
        )

        earned = build_report(self.sales, "commissions", {
            "period": "custom", "start_date": "2025-10-01", "end_date": "2025-10-01",
        })
        self.assertEqual([(row["entry_type"], row["approved_amount"]) for row in earned["items"]], [("order", "1.000")])
        SalesOrder.objects.filter(pk=order.pk).update(amount_version=order.amount_version + 1)
        needs_review = build_report(self.sales, "commissions", {
            "period": "custom", "start_date": "2025-10-01", "end_date": "2025-10-01",
        })["items"][0]
        self.assertEqual(needs_review["status"], "needs_review")
        self.assertEqual(needs_review["approved_amount"], "1.000")
        self.assertTrue(needs_review["review_delta"])
        rereview = build_report(self.sales, "commissions", {
            "period": "custom", "start_date": "2025-10-02", "end_date": "2025-10-02",
        })
        self.assertEqual(len(rereview["items"]), 1)
        self.assertEqual(rereview["items"][0]["adjustment_kind"], "重审差额")
        self.assertEqual(rereview["items"][0]["delta_amount"], "0.200")
        self.assertEqual(rereview["items"][0]["completion_status"], "已完成")
        correction = build_report(self.sales, "commissions", {
            "period": "custom", "start_date": "2025-10-03", "end_date": "2025-10-03",
        })
        self.assertEqual(correction["items"][0]["delta_amount"], "-0.400")
        self.assertEqual(correction["summary"]["adjustment_delta_not_added"], "-0.400")
        self.assertEqual(correction["summary"]["states"], {})

    def test_commission_visibility_requires_its_own_permission(self):
        self.new_order()
        _, _, other_order = self.preview_and_confirm(actor=self.sales_two)
        self.client.force_login(self.sales)
        response = self.client.get("/api/v1/reports/commissions")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 1)

        view_all_orders = Permission.objects.get(content_type__app_label="crm", codename="view_all_sales_orders")
        view_all_commissions = Permission.objects.get(content_type__app_label="crm", codename="view_all_commissions")
        self.sales.user_permissions.add(view_all_commissions)
        self.sales = self.User.objects.get(pk=self.sales.pk)
        self.client.force_login(self.sales)
        response = self.client.get("/api/v1/reports/commissions")
        self.assertEqual(response.json()["total"], 1)
        self.assertNotIn(str(other_order.pk), [row["order_id"] for row in response.json()["items"]])

        self.sales.user_permissions.add(view_all_orders)
        self.sales = self.User.objects.get(pk=self.sales.pk)
        self.client.force_login(self.sales)
        response = self.client.get("/api/v1/reports/commissions")
        self.assertEqual(response.json()["total"], 2)
        self.assertIn(str(other_order.pk), [row["order_id"] for row in response.json()["items"]])

        self.sales.user_permissions.remove(view_all_commissions)
        self.sales = self.User.objects.get(pk=self.sales.pk)
        self.client.force_login(self.sales)
        response = self.client.get("/api/v1/reports/commissions")
        self.assertEqual(response.json()["total"], 1)
        self.assertNotIn(str(other_order.pk), [row["order_id"] for row in response.json()["items"]])

    def test_export_keeps_all_rows_text_numbers_and_negative_amount(self):
        self.balance.sku.barcode = "0012345678901"
        self.balance.sku.save(update_fields=("barcode",))
        second_warehouse = Warehouse.objects.create(name="Synthetic export warehouse", address="Synthetic address")
        InventoryBalance.objects.create(sku=self.sku, warehouse=second_warehouse, on_hand=1, cost_amount=Decimal("1.235"))
        self.client.force_login(self.sales)
        page = self.client.get("/api/v1/reports/inventory?page_size=1")
        self.assertEqual(page.status_code, 200, page.content.decode())
        self.assertEqual(page.json()["total"], 2)
        export = self.client.get("/api/v1/reports/inventory/export")
        self.assertEqual(export.status_code, 200)
        from io import BytesIO
        from openpyxl import load_workbook

        workbook = load_workbook(BytesIO(export.content), data_only=False)
        sheet = workbook.active
        self.assertEqual(sheet.max_row, 8)
        self.assertEqual(sheet["E7"].value, "0012345678901")
        self.assertEqual(sheet["E7"].data_type, "s")
        data = {
            "title": "测试导出",
            "metadata": {
                "period_label": "全部日期", "date_basis": "合成验证", "filter_summary": "=1+1",
                "queried_at": "2026-10-05T12:00:00+08:00",
            },
            "columns": [("id", "编号", "text"), ("amount", "差额", "amount")],
            "items": [{"id": "0012", "amount": "-1.235"}],
        }
        workbook = load_workbook(BytesIO(workbook_bytes(data)), data_only=False)
        sheet = workbook.active
        self.assertEqual(sheet["A7"].value, "0012")
        self.assertEqual(sheet["A7"].data_type, "s")
        self.assertEqual(sheet["B7"].value, -1.235)
        self.assertEqual(sheet["B7"].data_type, "n")
        self.assertIn(".000", sheet["B7"].number_format)
        self.assertEqual(sheet["B4"].value, "=1+1")
        self.assertEqual(sheet["B4"].data_type, "s")
        data["items"] = [{"id": "0012", "amount": "123456789012345.678"}]
        with self.assertRaises(OperationError) as raised:
            workbook_bytes(data)
        self.assertEqual(raised.exception.code, "export_amount_precision")
