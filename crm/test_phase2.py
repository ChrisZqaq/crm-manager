import io
import os
import tempfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4
from xml.sax.saxutils import escape

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections
from django.test import Client, TransactionTestCase, override_settings
from django.utils import timezone
from PIL import Image

from .logging import BoundedRotatingFileHandler
from .models import (
    Brand,
    BusinessControl,
    BusinessOperation,
    DamagedRecord,
    InventoryBalance,
    InventoryHold,
    InventoryMovement,
    OpeningBatch,
    Product,
    Receipt,
    ReceiptImage,
    ReceiptLine,
    SKU,
    Supplier,
    Warehouse,
)
from .opening import confirm_opening, preview_opening
from .operations import (
    OperationError,
    confirm_receipt,
    create_hold,
    damaged_rows,
    hold_rows,
    inventory_rows,
    preview_receipt,
    release_hold,
    update_global_low_stock,
    update_sku_low_stock,
)
from .permissions import ROLE_OWNER, ROLE_SALES, ROLE_WAREHOUSE


PRODUCT_HEADERS = (
    "商品内部编号", "IP品牌", "商品类型", "系列", "产品名", "产品别名", "语言版本", "官方编号",
    "箱规格", "盒规格", "包规格", "包类型", "售卖单位", "包装条码", "规格别名",
)
STOCK_HEADERS = (
    "仓库名", "仓库地址", "商品内部编号", "实际在库数量", "单位成本", "客户预留数量",
    "囤货数量", "囤货原因", "破损数量", "备注",
)


class Formula:
    def __init__(self, expression):
        self.expression = expression


def _column_name(index):
    result = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _sheet_xml(rows):
    xml_rows = []
    for row_number, row in enumerate(rows, 1):
        cells = []
        for column, value in enumerate(row):
            if value is None:
                continue
            reference = f"{_column_name(column)}{row_number}"
            if isinstance(value, Formula):
                cells.append(f'<c r="{reference}"><f>{escape(value.expression)}</f><v></v></c>')
            elif isinstance(value, str):
                cells.append(f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">{escape(value)}</t></is></c>')
            else:
                cells.append(f'<c r="{reference}"><v>{escape(str(value))}</v></c>')
        xml_rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(xml_rows)}</sheetData></worksheet>'
    )


def opening_workbook(product_rows, stock_rows):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="商品档案" sheetId="1" r:id="rId1"/>'
            '<sheet name="期初库存" sheetId="2" r:id="rId2"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Target="worksheets/sheet2.xml"/>'
            '</Relationships>',
        )
        archive.writestr("xl/worksheets/sheet1.xml", _sheet_xml([PRODUCT_HEADERS, *product_rows]))
        archive.writestr("xl/worksheets/sheet2.xml", _sheet_xml([STOCK_HEADERS, *stock_rows]))
    return buffer.getvalue()


def product_row(code="TST-0001", *, brand="Synthetic IP", name="Sample Booster", aliases="A；B", barcode="00001234"):
    return [code, brand, "补充包", "Set 1", name, aliases, "简体中文", "", "20盒/箱", "18包/中盒", "5张/包", "瘦包", "盒", barcode, "标准版"]


def stock_row(code="TST-0001", *, warehouse="Synthetic Warehouse", address="Synthetic address", quantity=10, cost="100.000", reserved=0, hoarded=0, hoard_reason="", damaged=0, note=""):
    return [warehouse, address, code, quantity, cost, reserved, hoarded, hoard_reason, damaged, note]


class Phase2Base(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.User = get_user_model()
        self.user = self.User.objects.create_user(username="warehouse-test", password="test-only-password")
        self.user.groups.add(Group.objects.get(name=ROLE_WAREHOUSE))
        self.owner = self.User.objects.create_user(username="owner-test", password="test-only-password")
        self.owner.groups.add(Group.objects.get(name=ROLE_OWNER))
        self.brand = Brand.objects.create(name="Synthetic IP", sku_prefix="TST-")
        self.product = Product.objects.create(
            brand=self.brand,
            type=Product.Type.BOOSTER,
            series="Set 1",
            name="Sample Booster",
            language="简体中文",
            aliases=["A", "B"],
        )
        self.sku = SKU.objects.create(
            product=self.product,
            code="TST-0001",
            box_spec="20盒/箱",
            inner_box_spec="18包/中盒",
            pack_spec="5张/包",
            pack_type=SKU.PackType.SLIM,
            unit="盒",
            barcode="00001234",
            aliases=["标准版"],
        )
        self.warehouse = Warehouse.objects.create(name="Synthetic Warehouse", address="Synthetic address")
        self.second_warehouse = Warehouse.objects.create(name="Synthetic Warehouse 2", address="Synthetic address 2")
        self.supplier = Supplier.objects.create(name="Synthetic Supplier")

    def new_sku(self, number):
        return SKU.objects.create(product=self.product, code=f"TST-{number:04d}", unit="盒")

    def receipt_payload(self, sku, *, quantity_value, price, warehouse=None, freight="0", reserved=0, damaged=0, threshold=None):
        return {
            "warehouse_id": (warehouse or self.warehouse).pk,
            "supplier_id": self.supplier.pk,
            "lines": [
                {
                    "sku_id": sku.pk,
                    "quantity": quantity_value,
                    "purchase_price": str(price),
                    "inbound_freight": str(freight),
                    "reserved_quantity": reserved,
                    "damaged_quantity": damaged,
                    "low_stock_threshold": threshold,
                }
            ],
        }

    def preview_and_receive(self, payload, *, operation_key=None):
        preview = preview_receipt(self.user, payload)
        result = confirm_receipt(self.user, operation_key or uuid4(), preview["preview_token"])
        return preview, result


class OpeningPhase2Tests(Phase2Base):
    def setUp(self):
        super().setUp()
        SKU.objects.all().delete()
        Product.objects.all().delete()
        Brand.objects.all().delete()
        Warehouse.objects.all().delete()
        Supplier.objects.all().delete()

    def test_preview_requires_manual_ambiguous_product_and_barcode_confirmation_then_confirms_once(self):
        brand = Brand.objects.create(name="Synthetic IP", sku_prefix="TST-")
        first_product = Product.objects.create(brand=brand, type=Product.Type.BOOSTER, series="Set 1", name="Sample Booster", language="简体中文", aliases=["A", "B"])
        second_product = Product.objects.create(brand=brand, type=Product.Type.BOOSTER, series="Set 1", name="Sample Booster", language="简体中文", aliases=["A", "B"])
        SKU.objects.create(product=first_product, code="TST-0008", unit="盒", barcode="00001234")
        SKU.objects.create(product=second_product, code="TST-0009", unit="盒", barcode="00001234")
        content = opening_workbook([product_row()], [stock_row(reserved=2, hoarded=3, hoard_reason="manual hold", damaged=1)])

        before = (Brand.objects.count(), Product.objects.count(), SKU.objects.count(), Warehouse.objects.count(), InventoryBalance.objects.count(), OpeningBatch.objects.count())
        first_preview = preview_opening(self.user, content, "opening.xlsx")
        self.assertFalse(first_preview["ready"])
        requirements = first_preview["mapping_requirements"]
        product_requirement = next(item for item in requirements if item["kind"] == "product")
        barcode_requirement = next(item for item in requirements if item["kind"] == "duplicate_barcode")
        self.assertEqual(len(product_requirement["candidates"]), 2)
        self.assertEqual(barcode_requirement["key"], "00001234")
        self.assertEqual(before, (Brand.objects.count(), Product.objects.count(), SKU.objects.count(), Warehouse.objects.count(), InventoryBalance.objects.count(), OpeningBatch.objects.count()))

        mapped = preview_opening(
            self.user,
            content,
            "opening.xlsx",
            {
                "products": {product_requirement["key"]: first_product.pk},
                "confirmed_duplicate_barcodes": ["00001234"],
            },
        )
        self.assertTrue(mapped["ready"])
        self.assertEqual(mapped["stock_line_count"], 1)
        self.assertEqual(mapped["cost_total"], "1000.000")
        self.assertEqual(before, (Brand.objects.count(), Product.objects.count(), SKU.objects.count(), Warehouse.objects.count(), InventoryBalance.objects.count(), OpeningBatch.objects.count()))

        key = uuid4()
        result = confirm_opening(self.user, key, mapped["preview_token"], request_id="opening-test-1")
        replay = confirm_opening(self.user, key, mapped["preview_token"], request_id="opening-test-2")
        self.assertEqual(result, replay)
        balance = InventoryBalance.objects.get(warehouse__name="Synthetic Warehouse", sku__code="TST-0001")
        self.assertEqual((balance.on_hand, balance.reserved, balance.hoarded, balance.damaged, balance.cost_amount), (10, 2, 3, 1, Decimal("1000.000")))
        self.assertEqual(balance.on_hand - balance.reserved - balance.hoarded - balance.damaged, 4)
        self.assertEqual(InventoryHold.objects.filter(balance=balance).count(), 2)
        self.assertEqual(sum(item.remaining_quantity for item in InventoryHold.objects.filter(balance=balance)), 5)
        self.assertEqual(DamagedRecord.objects.get(balance=balance).remaining_quantity, 1)
        self.assertEqual(InventoryMovement.objects.filter(operation__opening_batch_id=result["opening_batch_id"]).count(), 1)
        self.assertTrue(BusinessControl.objects.get(pk=1).opening_completed)
        self.assertEqual(BusinessControl.objects.get(pk=1).last_sequence, result["sequence"])

    def test_parser_reports_missing_cost_and_rejects_formula_without_writing(self):
        content = opening_workbook([product_row()], [stock_row(cost="", quantity=3)])
        with self.assertRaises(OperationError) as raised:
            preview_opening(self.user, content, "opening.xlsx", {"brand_prefixes": {"Synthetic IP": "TST-"}})
        row_errors = raised.exception.fields["rows"]
        self.assertTrue(any(item["sheet"] == "期初库存" and item["field"] == "单位成本" for item in row_errors))
        formula = stock_row(quantity=Formula("1+2"))
        content = opening_workbook([product_row()], [formula])
        with self.assertRaises(OperationError) as raised:
            preview_opening(self.user, content, "opening.xlsx", {"brand_prefixes": {"Synthetic IP": "TST-"}})
        self.assertTrue(any(item["message"].startswith("不接受公式") for item in raised.exception.fields["rows"]))
        self.assertEqual((Brand.objects.count(), Product.objects.count(), SKU.objects.count(), Warehouse.objects.count(), InventoryBalance.objects.count()), (0, 0, 0, 0, 0))


class ReceiptAndHoldPhase2Tests(Phase2Base):
    def test_weighted_average_idempotency_and_per_warehouse_cost(self):
        preview1, result1 = self.preview_and_receive(self.receipt_payload(self.sku, quantity_value=10, price="100.000"))
        key = uuid4()
        preview2 = preview_receipt(self.user, self.receipt_payload(self.sku, quantity_value=10, price="200.000"))
        result2 = confirm_receipt(self.user, key, preview2["preview_token"])
        replay = confirm_receipt(self.user, key, preview2["preview_token"])
        self.assertEqual(result2, replay)
        balance = InventoryBalance.objects.get(warehouse=self.warehouse, sku=self.sku)
        self.assertEqual((balance.on_hand, balance.cost_amount), (20, Decimal("3000.000")))
        row = inventory_rows(self.user, {})[0]
        self.assertEqual((row["available"], row["average_cost"], row["cost_amount"]), (20, "150.000", "3000.000"))

        other = self.receipt_payload(self.sku, quantity_value=5, price="300.000", warehouse=self.second_warehouse)
        self.preview_and_receive(other)
        first, second = InventoryBalance.objects.order_by("warehouse_id").filter(sku=self.sku)
        self.assertEqual((first.cost_amount, second.cost_amount), (Decimal("3000.000"), Decimal("1500.000")))
        self.assertEqual(first.cost_amount / first.on_hand, Decimal("150"))
        self.assertEqual(second.cost_amount / second.on_hand, Decimal("300"))
        conflict_preview = preview_receipt(self.user, self.receipt_payload(self.sku, quantity_value=1, price="1.000"))
        with self.assertRaises(OperationError) as raised:
            confirm_receipt(self.user, key, conflict_preview["preview_token"])
        self.assertEqual(raised.exception.code, "idempotency_conflict")
        self.assertEqual(Receipt.objects.count(), 3)

    def test_reservation_release_updates_only_target_hold_and_low_stock_uses_available(self):
        sku = self.new_sku(2)
        payload = self.receipt_payload(sku, quantity_value=60, price="10.000", reserved=55)
        self.preview_and_receive(payload)
        balance = InventoryBalance.objects.get(warehouse=self.warehouse, sku=sku)
        hold = InventoryHold.objects.get(balance=balance)
        self.assertEqual((balance.on_hand, balance.reserved, balance.damaged), (60, 55, 0))
        self.assertEqual(balance.on_hand - balance.reserved - balance.occupied - balance.hoarded - balance.damaged, 5)
        before_rows = inventory_rows(self.user, {"sku_id": str(sku.pk)})
        self.assertEqual(before_rows[0]["available"], 5)
        self.assertTrue(before_rows[0]["low_stock"])
        released = release_hold(
            self.user,
            hold.pk,
            {"quantity": 20, "expected_version": hold.version, "operation_key": str(uuid4())},
        )
        hold.refresh_from_db()
        balance.refresh_from_db()
        self.assertEqual((released["remaining_quantity"], hold.remaining_quantity, hold.version), (35, 35, 2))
        self.assertEqual((balance.reserved, balance.on_hand - balance.reserved), (35, 25))
        self.assertEqual(hold_rows()[0]["remaining_quantity"], 35)
        self.assertEqual(InventoryMovement.objects.filter(hold=hold, event_kind="hold_release").count(), 1)
        with self.assertRaises(OperationError) as raised:
            release_hold(self.user, hold.pk, {"quantity": 36, "expected_version": hold.version, "operation_key": str(uuid4())})
        self.assertEqual(raised.exception.code, "stock_conflict")

    def test_zero_price_warning_and_same_sku_threshold_conflict(self):
        preview = preview_receipt(self.user, self.receipt_payload(self.sku, quantity_value=1, price="0.000"))
        self.assertIn("存在零采购价，请核对。", preview["warnings"])
        payload = self.receipt_payload(self.sku, quantity_value=1, price="1.000")
        payload["lines"].append({**payload["lines"][0], "low_stock_threshold": 4})
        payload["lines"][0]["low_stock_threshold"] = 5
        with self.assertRaises(OperationError) as raised:
            preview_receipt(self.user, payload)
        self.assertIn("低库存阈值不一致", str(raised.exception))

    def test_low_stock_settings_are_dynamic_and_global_off_keeps_threshold(self):
        self.preview_and_receive(self.receipt_payload(self.sku, quantity_value=4, price="2.000"))
        sku_rows = inventory_rows(self.user, {})
        self.assertTrue(sku_rows[0]["low_stock"])
        updated = update_sku_low_stock(self.user, self.sku.pk, {"enabled": False, "threshold": 0}, uuid4())
        self.assertEqual((updated["enabled"], updated["threshold"]), (False, 0))
        owner_rows = inventory_rows(self.owner, {})
        self.assertFalse(owner_rows[0]["low_stock"])
        global_result = update_global_low_stock(self.owner, {"enabled": False, "threshold": 8}, uuid4())
        self.assertFalse(global_result["enabled"])
        self.assertEqual(global_result["threshold"], 8)
        self.sku.refresh_from_db()
        self.assertEqual((self.sku.low_stock_enabled, self.sku.low_stock_threshold), (False, 0))
        self.assertFalse(inventory_rows(self.owner, {})[0]["low_stock"])


class PostgreSQLConcurrencyPhase2Tests(Phase2Base):
    def test_concurrent_first_receipts_create_one_balance_and_stale_preview_is_rejected(self):
        payload = self.receipt_payload(self.sku, quantity_value=3, price="20.000")
        first = preview_receipt(self.user, payload)
        second = preview_receipt(self.user, payload)
        barrier = threading.Barrier(2)

        def worker(key):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    return confirm_receipt(self.user, key, first["preview_token"])["receipt_id"]
                except OperationError as exc:
                    return exc.code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            outputs = list(executor.map(worker, (uuid4(), uuid4())))
        self.assertEqual(sum(isinstance(value, int) for value in outputs), 1)
        self.assertEqual(outputs.count("stale_preview"), 1)
        self.assertEqual(InventoryBalance.objects.count(), 1)
        self.assertEqual(InventoryBalance.objects.get().on_hand, 3)
        fresh = preview_receipt(self.user, payload)
        confirm_receipt(self.user, uuid4(), fresh["preview_token"])
        self.assertEqual(InventoryBalance.objects.count(), 1)
        self.assertEqual(InventoryBalance.objects.get().on_hand, 6)

    def test_concurrent_releases_cannot_exceed_one_hold_remaining_amount(self):
        payload = self.receipt_payload(self.sku, quantity_value=10, price="10.000")
        self.preview_and_receive(payload)
        hold_result = create_hold(
            self.user,
            {"warehouse_id": self.warehouse.pk, "sku_id": self.sku.pk, "type": "customer_reservation", "quantity": 10, "reason": "synthetic"},
            uuid4(),
        )
        hold = InventoryHold.objects.get(pk=hold_result["hold_id"])
        barrier = threading.Barrier(2)

        def worker(key):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    return release_hold(self.user, hold.pk, {"quantity": 7, "expected_version": 1, "operation_key": str(key)})["remaining_quantity"]
                except OperationError as exc:
                    return exc.code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            outputs = list(executor.map(worker, (uuid4(), uuid4())))
        self.assertEqual(outputs.count(3), 1)
        self.assertEqual(outputs.count("version_conflict"), 1)
        hold.refresh_from_db()
        balance = InventoryBalance.objects.get(pk=hold.balance_id)
        self.assertEqual((hold.remaining_quantity, balance.reserved, balance.on_hand), (3, 3, 10))

    def test_second_line_failure_rolls_back_everything_and_key_can_retry(self):
        second_sku = self.new_sku(2)
        payload = self.receipt_payload(self.sku, quantity_value=2, price="10.000")
        payload["lines"].append({"sku_id": second_sku.pk, "quantity": 3, "purchase_price": "15.000", "inbound_freight": "2.000", "reserved_quantity": 0, "damaged_quantity": 0})
        preview = preview_receipt(self.user, payload)
        key = uuid4()
        manager = ReceiptLine.objects
        real_create = manager.create
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("synthetic second-line failure")
            return real_create(*args, **kwargs)

        with self.assertLogs("crm.business", level="ERROR") as captured:
            with patch.object(manager, "create", side_effect=fail_second):
                with self.assertRaisesRegex(RuntimeError, "synthetic"):
                    confirm_receipt(self.user, key, preview["preview_token"], request_id="phase2.rollback")
        self.assertIn("request_id=phase2.rollback", captured.output[0])
        self.assertIn("exception_location=", captured.output[0])
        self.assertRegex(captured.output[0], r"document_id=[1-9][0-9]*")
        self.assertEqual((Receipt.objects.count(), ReceiptLine.objects.count(), BusinessOperation.objects.count(), InventoryBalance.objects.count()), (0, 0, 0, 0))
        self.assertEqual(BusinessControl.objects.get(pk=1).last_sequence, 0)
        result = confirm_receipt(self.user, key, preview["preview_token"])
        self.assertEqual(result["line_count"], 2)
        self.assertEqual(InventoryBalance.objects.count(), 2)


class ImageAndLoggingPhase2Tests(Phase2Base):
    def _png(self):
        buffer = io.BytesIO()
        Image.new("RGB", (2, 2), color=(1, 2, 3)).save(buffer, format="PNG")
        return buffer.getvalue()

    @override_settings(MEDIA_ROOT="/tmp/aluola-crm-phase2-media")
    def test_receipt_images_are_decoded_private_and_authorized(self):
        client = Client()
        client.force_login(self.user)
        upload = SimpleUploadedFile("proof.png", self._png(), content_type="image/png")
        response = client.post("/api/v1/images", {"file": upload})
        self.assertEqual(response.status_code, 200, response.content)
        image_id = int(response.json()["image_id"])
        image = ReceiptImage.objects.get(pk=image_id)
        self.assertTrue(image.file.name.startswith("receipt-images/"))
        self.assertTrue(os.path.exists(image.file.path))
        self.assertEqual(client.get(f"/api/v1/images/{image.pk}").status_code, 200)

        bad = SimpleUploadedFile("proof.png", b"not an image", content_type="image/png")
        self.assertEqual(client.post("/api/v1/images", {"file": bad}).status_code, 400)

        payload = self.receipt_payload(self.sku, quantity_value=1, price="1.000")
        payload["image_ids"] = [image.pk]
        preview = preview_receipt(self.user, payload)
        confirm_receipt(self.user, uuid4(), preview["preview_token"])
        image.refresh_from_db()
        self.assertIsNotNone(image.receipt_id)
        sales = get_user_model().objects.create_user(username="sales-image-test", password="test-only-password")
        sales.groups.add(Group.objects.get(name=ROLE_SALES))
        sales_client = Client()
        sales_client.force_login(sales)
        self.assertEqual(sales_client.get(f"/api/v1/images/{image.pk}").status_code, 403)

    def test_orphan_cleanup_and_log_rotation_retention(self):
        client = Client()
        client.force_login(self.user)
        response = client.post("/api/v1/images", {"file": SimpleUploadedFile("proof.png", self._png(), content_type="image/png")})
        image = ReceiptImage.objects.get(pk=response.json()["image_id"])
        old_time = timezone.now() - timedelta(hours=25)
        ReceiptImage.objects.filter(pk=image.pk).update(created_at=old_time)
        from django.core.management import call_command

        call_command("cleanup_orphan_receipt_images", verbosity=0)
        self.assertFalse(ReceiptImage.objects.filter(pk=image.pk).exists())
        self.assertFalse(os.path.exists(image.file.path))

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "business.log")
            handler = BoundedRotatingFileHandler(path, max_bytes=100, backup_count=2, retention_days=7)
            logger = __import__("logging").getLogger(f"phase2-rotation-{uuid4()}")
            logger.setLevel("INFO")
            logger.addHandler(handler)
            for index in range(12):
                logger.info("synthetic business log record %02d", index)
            files = [os.path.join(directory, name) for name in os.listdir(directory)]
            self.assertLessEqual(len(files), 3)
            old_backup = next((item for item in files if item != path), None)
            self.assertIsNotNone(old_backup)
            os.utime(old_backup, (time.time() - 8 * 24 * 60 * 60, time.time() - 8 * 24 * 60 * 60))
            handler._prune_expired()
            self.assertFalse(os.path.exists(old_backup))
            logger.removeHandler(handler)
            handler.close()
