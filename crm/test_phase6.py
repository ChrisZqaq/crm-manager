from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib.auth.models import Permission
from django.test import SimpleTestCase
from django.utils import timezone

from .models import (
    BusinessOperation,
    CommissionAdjustment,
    CommissionReview,
    InventoryBalance,
    InventoryHold,
    SalesOrder,
    Warehouse,
)
from .operations import OperationError
from .reports import _date_criteria, build_report, workbook_bytes
from .test_phase4 import Phase4Base
from .fulfillment import deliver_on_site, ship_sales_order


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
        self.client.force_login(self.sales)
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
        data = build_report(self.sales, "inventory", {})
        row = next(item for item in data["items"] if item["id"] == str(self.balance.pk))
        self.assertEqual(row["available"], 18)
        self.assertIn("囤货（Synthetic event stock） 2 盒", row["lock_reasons"])
        self.assertFalse(data["metadata"]["date_filter_available"])
        self.client.force_login(self.sales)
        page = self.client.get("/reports/inventory/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Synthetic event stock")

    def test_payment_report_does_not_invent_q07_date_basis(self):
        order = self.new_order()
        self.register(order)
        data = build_report(self.sales, "payments", {})
        self.assertEqual(len(data["items"]), 1)
        self.assertFalse(data["metadata"]["date_filter_available"])
        self.assertTrue(data["metadata"]["payment_date_basis_pending"])
        self.assertEqual(data["items"][0]["actual_received_at"], "")
        self.assertIn("非实收核实", dict((key, label) for key, label, _ in data["columns"]).get("registered_amount", ""))
        with self.assertRaises(OperationError) as raised:
            build_report(self.sales, "payments", {"period": "1m"})
        self.assertEqual(raised.exception.code, "date_filter_pending")
        self.client.force_login(self.sales)
        response = self.client.get("/api/v1/reports/payments?period=1m")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "date_filter_pending")
        page = self.client.get("/reports/payments/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Q07待确认")
        invalid_page = self.client.get("/reports/payments/?page_size=not-a-number")
        self.assertEqual(invalid_page.status_code, 200)
        self.assertContains(invalid_page, "page与page_size必须为整数")

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
