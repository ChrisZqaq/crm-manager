import io
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase, skipUnlessDBFeature
from openpyxl import load_workbook

from .models import (
    Brand,
    BusinessControl,
    BusinessOperation,
    InventoryBalance,
    InventoryMovement,
    Product,
    ProductLanguageOption,
    SKU,
    Warehouse,
)
from .operations import PREVIEW_SALT, OperationError, confirm_catalog_import
from .reports import build_report


TEMPLATE = Path(__file__).resolve().parents[1] / "docs/templates/商品批量导入模板-V1.xlsx"
def workbook_bytes(product_rows, spec_rows=()):
    workbook = load_workbook(TEMPLATE)
    products = workbook["产品导入"]
    for row_number, values in enumerate(product_rows, 2):
        for column, value in enumerate(values, 1):
            products.cell(row_number, column, value)
    specs = workbook["规格补充"]
    for row_number, values in enumerate(spec_rows, 2):
        for column, value in enumerate(values, 1):
            specs.cell(row_number, column, value)
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def product_row(*, file_id="P001", combo="肥包和瘦包", units=("是", "是", "是", "是"), name="合成商品"):
    return [file_id, "星海", "补充包", "合成系列", name, "", "简中", "SYN-001", combo, *units, "", "2026-11-01", "合成测试"]


class CatalogImportSetupMixin:
    def setUpCatalogImport(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(username=f"catalog-import-{uuid.uuid4().hex[:8]}", email="", password="test")
        self.client = Client()
        self.client.force_login(self.user)
        self.brand = Brand.objects.create(name="星海", sku_prefix="SEA")
        self.language = ProductLanguageOption.objects.get(name="简中")
        self.control = BusinessControl.objects.get(pk=1)

    def preview(self, contents):
        upload = SimpleUploadedFile("商品批量导入.xlsx", contents, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        return self.client.post("/api/v1/products/import/preview", {"file": upload})

    def confirm(self, preview, *, operation_key=None, confirmed=()):
        return self.client.post(
            "/api/v1/products/import/confirm",
            data=json.dumps({
                "operation_key": operation_key or str(uuid.uuid4()),
                "preview_token": preview["preview_token"],
                "confirmed_duplicate_sku_keys": list(confirmed),
            }),
            content_type="application/json",
        )


class CatalogImportTests(CatalogImportSetupMixin, TestCase):
    def setUp(self):
        self.setUpCatalogImport()

    def test_template_generates_all_eight_pack_unit_combinations_without_inventory(self):
        preview = self.preview(workbook_bytes([product_row()]))
        self.assertEqual(preview.status_code, 200)
        body = preview.json()
        self.assertTrue(body["ready"], body.get("errors"))
        self.assertEqual(body["product_count"], 1)
        self.assertEqual(body["sku_count"], 8)
        rows = body["products"][0]["skus"]
        self.assertEqual(
            {(item["pack_type"], item["unit"]) for item in rows},
            {(pack, unit) for pack in (SKU.PackType.FAT, SKU.PackType.SLIM) for unit in ("箱", "盒", "垃圾袋", "包")},
        )
        self.assertFalse(Product.objects.filter(name="合成商品").exists())
        before_balances = InventoryBalance.objects.count()
        before_movements = InventoryMovement.objects.count()
        response = self.confirm(body)
        self.assertEqual(response.status_code, 200, response.content)
        result = response.json()
        self.assertEqual(len(result["product_ids"]), 1)
        self.assertEqual(len(result["sku_ids"]), 8)
        self.assertEqual(SKU.objects.filter(product_id=result["product_ids"][0]).count(), 8)
        self.assertEqual(InventoryBalance.objects.count(), before_balances)
        self.assertEqual(InventoryMovement.objects.count(), before_movements)
        operation = BusinessOperation.objects.get(action="catalog_import")
        self.assertEqual(operation.control_id, self.control.pk)

    def test_import_apis_require_catalog_permissions(self):
        anonymous = Client()
        response = anonymous.post("/api/v1/products/import/preview")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "authentication_required")
        user = get_user_model().objects.create_user(username="no-catalog-import-permission", password="test")
        denied = Client()
        denied.force_login(user)
        response = denied.post("/api/v1/products/import/preview")
        self.assertEqual(response.status_code, 403)
        response = denied.post("/api/v1/products/import/confirm", data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 403)

    def test_formula_and_negative_dimensions_are_rejected_before_any_write(self):
        formula_bytes = workbook_bytes([product_row(combo="不区分", units=("否", "是", "否", "否"))])
        workbook = load_workbook(io.BytesIO(formula_bytes))
        workbook["产品导入"]["E2"] = "=1+1"
        output = io.BytesIO()
        workbook.save(output)
        workbook.close()
        formula_preview = self.preview(output.getvalue()).json()
        self.assertFalse(formula_preview["ready"])
        self.assertTrue(any(error["message"] == "导入表不能包含公式。" for error in formula_preview["errors"]))

        negative = workbook_bytes(
            [product_row(combo="不区分", units=("否", "是", "否", "否"))],
            [["P001", "不区分", "盒", "", "", "", "", -1, "", "", ""]],
        )
        negative_preview = self.preview(negative).json()
        self.assertFalse(negative_preview["ready"])
        self.assertTrue(any("有限正数" in error["message"] for error in negative_preview["errors"]))
        self.assertFalse(Product.objects.filter(name="合成商品").exists())

    def test_each_supplement_row_keeps_its_own_unit_barcode_and_dimensions(self):
        contents = workbook_bytes(
            [product_row(combo="仅肥包", units=("否", "是", "是", "否"))],
            [
                ["P001", "肥包", "盒", "", "", "整盒", "BOX-001", 120, 80, 40, 250],
                ["P001", "肥包", "垃圾袋", "", "", "", "BAG-002", "", "", "", ""],
            ],
        )
        preview = self.preview(contents).json()
        self.assertTrue(preview["ready"], preview.get("errors"))
        rows = {item["unit"]: item for item in preview["products"][0]["skus"]}
        self.assertEqual(rows["盒"]["barcode"], "BOX-001")
        self.assertEqual(rows["垃圾袋"]["barcode"], "BAG-002")
        self.assertEqual(rows["盒"]["aliases"], ["整盒"])
        self.assertEqual(rows["盒"]["package_length_mm"], "120.000")
        response = self.confirm(preview)
        self.assertEqual(response.status_code, 200, response.content)
        saved = {sku.unit: sku for sku in SKU.objects.filter(product_id=response.json()["product_ids"][0])}
        self.assertEqual(saved["盒"].barcode, "BOX-001")
        self.assertEqual(saved["垃圾袋"].barcode, "BAG-002")
        self.assertEqual(saved["盒"].package_length_mm, 120)
        self.assertEqual(saved["垃圾袋"].barcode, "BAG-002")

    def test_specification_distinction_creates_extra_sku_with_search_alias(self):
        contents = workbook_bytes(
            [product_row(combo="仅肥包", units=("是", "否", "否", "否"))],
            [["P001", "肥包", "箱", "40盒装", "", "大箱", "", "", "", "", ""]],
        )
        preview = self.preview(contents).json()
        self.assertTrue(preview["ready"], preview.get("errors"))
        rows = preview["products"][0]["skus"]
        self.assertEqual(len(rows), 2)
        extra = next(item for item in rows if "40盒装" in item["aliases"])
        self.assertIn("大箱", extra["aliases"])
        response = self.confirm(preview)
        self.assertEqual(response.status_code, 200, response.content)
        saved = SKU.objects.get(code=extra["code"])
        self.assertEqual(saved.aliases, ["40盒装", "大箱"])

    def test_duplicate_barcode_requires_per_sku_confirmation_and_lists_candidate(self):
        existing_product = Product.objects.create(
            brand=self.brand,
            type=Product.Type.BOOSTER,
            series="已有系列",
            name="已有商品",
            language=self.language,
        )
        SKU.objects.create(product=existing_product, code="SEA0001", unit="盒", barcode="DUPLICATE")
        contents = workbook_bytes(
            [product_row(combo="仅肥包", units=("否", "是", "否", "否"), name="另一个商品")],
            [["P001", "肥包", "盒", "", "", "", "DUPLICATE", "", "", "", ""]],
        )
        preview_response = self.preview(contents)
        body = preview_response.json()
        self.assertTrue(body["ready"], body.get("errors"))
        self.assertEqual(body["barcode_warnings"][0]["candidate_count"], 1)
        self.assertEqual(body["barcode_warnings"][0]["candidates"][0]["code"], "SEA0001")
        missing_confirmation = self.confirm(body)
        self.assertEqual(missing_confirmation.status_code, 409)
        self.assertEqual(missing_confirmation.json()["error"]["code"], "duplicate_barcode_confirmation_required")
        confirmed = self.confirm(body, confirmed=[body["barcode_warnings"][0]["sku_key"]])
        self.assertEqual(confirmed.status_code, 200, confirmed.content)

    def test_preview_token_keeps_existing_candidate_details_outside_the_signed_payload(self):
        existing_product = Product.objects.create(
            brand=self.brand,
            type=Product.Type.BOOSTER,
            series="候选专属系列",
            name="候选专属商品",
            language=self.language,
        )
        SKU.objects.create(product=existing_product, code="SEA0099", unit="盒", barcode="PRIVATE-BARCODE")
        contents = workbook_bytes(
            [product_row(combo="仅肥包", units=("否", "是", "否", "否"), name="新导入商品")],
            [["P001", "肥包", "盒", "", "", "", "PRIVATE-BARCODE", "", "", "", ""]],
        )
        body = self.preview(contents).json()
        self.assertTrue(body["ready"], body.get("errors"))
        data = signing.loads(body["preview_token"], salt=PREVIEW_SALT)["data"]
        self.assertIn("product_candidate_hash", data)
        self.assertIn("barcode_candidate_hash", data)
        self.assertNotIn("product_candidates", data)
        self.assertNotIn("barcode_candidates", data)
        encoded = json.dumps(data, ensure_ascii=False)
        self.assertNotIn("候选专属商品", encoded)
        self.assertNotIn("SEA0099", encoded)

    def test_import_replay_returns_same_result_without_more_rows_or_sequence(self):
        preview = self.preview(workbook_bytes([product_row(combo="不区分", units=("否", "是", "否", "否"))])).json()
        key = str(uuid.uuid4())
        first = self.confirm(preview, operation_key=key)
        self.assertEqual(first.status_code, 200, first.content)
        sequence = BusinessControl.objects.get(pk=1).last_sequence
        product_count = Product.objects.count()
        sku_count = SKU.objects.count()
        second = self.confirm(preview, operation_key=key)
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(second.json(), first.json())
        self.assertEqual(BusinessControl.objects.get(pk=1).last_sequence, sequence)
        self.assertEqual(Product.objects.count(), product_count)
        self.assertEqual(SKU.objects.count(), sku_count)

    def test_late_sku_validation_failure_rolls_back_whole_import(self):
        preview = self.preview(workbook_bytes([product_row(combo="不区分", units=("是", "是", "否", "否"))])).json()
        product_count, sku_count = Product.objects.count(), SKU.objects.count()
        sequence = self.control.last_sequence
        from . import operations

        original = operations._catalog_model_validation

        def fail_on_second_sku(instance, **kwargs):
            if isinstance(instance, SKU) and instance.unit == "盒":
                raise OperationError("synthetic late validation failure")
            return original(instance, **kwargs)

        with patch("crm.operations._catalog_model_validation", side_effect=fail_on_second_sku):
            response = self.confirm(preview)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Product.objects.count(), product_count)
        self.assertEqual(SKU.objects.count(), sku_count)
        self.assertEqual(BusinessControl.objects.get(pk=1).last_sequence, sequence)
        self.assertFalse(BusinessOperation.objects.filter(action="catalog_import").exists())

    def test_used_user_supplied_sku_code_is_reported_without_overwrite(self):
        old = Product.objects.create(brand=self.brand, type=Product.Type.MERCHANDISE, name="旧商品")
        SKU.objects.create(product=old, code="SEA0001", unit="盒")
        contents = workbook_bytes(
            [product_row(combo="不区分", units=("否", "是", "否", "否"), name="新商品")],
            [["P001", "不区分", "盒", "", "SEA0001", "", "", "", "", "", ""]],
        )
        preview = self.preview(contents).json()
        self.assertFalse(preview["ready"])
        self.assertTrue(any("已被使用" in error["message"] for error in preview["errors"]))
        self.assertFalse(Product.objects.filter(name="新商品").exists())

    def test_identical_workbook_content_cannot_create_a_second_batch(self):
        contents = workbook_bytes([product_row(combo="不区分", units=("否", "是", "否", "否"))])
        first_preview = self.preview(contents).json()
        self.assertTrue(first_preview["ready"])
        first = self.confirm(first_preview)
        self.assertEqual(first.status_code, 200, first.content)
        second_preview = self.preview(contents).json()
        self.assertTrue(second_preview["already_imported"])
        self.assertFalse(second_preview["ready"])
        self.assertEqual(Product.objects.filter(name="合成商品").count(), 1)

    def test_inventory_api_exposes_sku_labels_without_changing_report_columns_or_totals(self):
        product = Product.objects.create(brand=self.brand, type=Product.Type.MERCHANDISE, name="库存展示商品")
        sku = SKU.objects.create(
            product=product,
            code="SEA0001",
            pack_type=SKU.PackType.FAT,
            unit="箱",
            aliases=["40盒装"],
        )
        warehouse = Warehouse.objects.create(name="合成仓", address="合成地址")
        InventoryBalance.objects.create(sku=sku, warehouse=warehouse, on_hand=3, cost_amount=Decimal("3.000"))
        response = self.client.get("/api/v1/inventory")
        self.assertEqual(response.status_code, 200, response.content)
        row = next(item for item in response.json()["items"] if item["sku_id"] == str(sku.pk))
        self.assertEqual(row["pack_type_label"], "肥包")
        self.assertEqual(row["aliases"], ["40盒装"])
        report = build_report(self.user, "inventory", {})
        self.assertFalse(any(column[0] in {"pack_type_label", "aliases"} for column in report["columns"]))
        self.assertEqual(report["summary"]["by_unit"], [
            {"unit": "箱", "on_hand": 3, "available": 3, "occupied": 0, "reserved": 0, "damaged": 0},
        ])


class CatalogImportConcurrencyTests(CatalogImportSetupMixin, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.setUpCatalogImport()

    @skipUnlessDBFeature("has_select_for_update")
    def test_two_previews_for_same_next_number_cannot_both_confirm(self):
        first_contents = workbook_bytes([product_row(combo="不区分", units=("否", "是", "否", "否"), name="并发商品A")])
        second_contents = workbook_bytes([product_row(combo="不区分", units=("否", "是", "否", "否"), name="并发商品B")])
        first_preview = self.preview(first_contents).json()
        second_preview = self.preview(second_contents).json()
        self.assertTrue(first_preview["ready"])
        self.assertTrue(second_preview["ready"])
        first_code = first_preview["products"][0]["skus"][0]["code"]
        self.assertEqual(second_preview["products"][0]["skus"][0]["code"], first_code)
        barrier = threading.Barrier(2)

        def confirm(args):
            key, preview_token = args
            close_old_connections()
            barrier.wait()
            try:
                result = confirm_catalog_import(self.user, key, preview_token, [])
                return "ok", result
            except OperationError as exc:
                return exc.code, exc.status
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(confirm, (
                (str(uuid.uuid4()), first_preview["preview_token"]),
                (str(uuid.uuid4()), second_preview["preview_token"]),
            )))
        self.assertCountEqual([item[0] for item in results], ["ok", "stale_preview"])
        self.assertEqual(SKU.objects.filter(code=first_code).count(), 1)
        self.assertEqual(Product.objects.filter(name__in=("并发商品A", "并发商品B")).count(), 1)
        self.assertEqual(BusinessOperation.objects.filter(action="catalog_import").count(), 1)
