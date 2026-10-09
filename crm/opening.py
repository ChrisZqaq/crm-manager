import hashlib
import io
import json
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

from django.core import signing
from django.db import transaction

from .models import (
    Brand,
    BusinessControl,
    BusinessOperation,
    DamagedRecord,
    InventoryBalance,
    InventoryHold,
    InventoryMovement,
    OpeningBatch,
    OpeningLine,
    Product,
    ProductLanguageOption,
    SKU,
    Warehouse,
)
from .operations import (
    CENT,
    MAX_MONEY,
    MAX_QUANTITY,
    PREVIEW_SALT,
    OperationError,
    _lock_control,
    _next_sequence,
    _operation_replay,
    _read_preview,
    _require,
    _run_write,
    _check_balance_result,
    amount,
    canonical_hash,
    identifier,
    operation_uuid,
    quantity,
    _readonly_snapshot,
    set_operation_document_id,
)


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
PRODUCT_SHEET = "商品档案"
STOCK_SHEET = "期初库存"
PRODUCT_HEADERS = (
    "商品内部编号", "IP品牌", "商品类型", "系列", "产品名", "产品别名", "语言版本", "官方编号",
    "箱规格", "盒规格", "包规格", "包类型", "售卖单位", "包装条码", "规格别名",
)
STOCK_HEADERS = (
    "仓库名", "仓库地址", "商品内部编号", "实际在库数量", "单位成本", "客户预留数量",
    "囤货数量", "囤货原因", "破损数量", "备注",
)
PRODUCT_TYPES = {"补充包": Product.Type.BOOSTER, "周边": Product.Type.MERCHANDISE, "礼盒·预组": Product.Type.GIFT_SET}
PACK_TYPES = {"": "", "瘦包": SKU.PackType.SLIM, "肥包": SKU.PackType.FAT, "空": SKU.PackType.EMPTY}


def _column_index(reference):
    letters = re.match(r"[A-Z]+", reference.upper())
    if not letters:
        return 0
    result = 0
    for character in letters.group(0):
        result = result * 26 + ord(character) - ord("A") + 1
    return result - 1


def _xml_text(node):
    return "".join(item.text or "" for item in node.findall(f".//{{{MAIN_NS}}}t"))


def _read_xlsx(content):
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
        names = set(archive.namelist())
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError):
        raise OperationError("文件不是可读取的Excel工作簿。", fields={"file": "请上传未损坏的.xlsx文件。"}) from None
    relationship_targets = {item.attrib["Id"]: item.attrib["Target"] for item in relationships.findall(f"{{{REL_NS}}}Relationship")}
    shared = []
    if "xl/sharedStrings.xml" in names:
        try:
            shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        except ET.ParseError:
            raise OperationError("Excel共享字符串表损坏。", fields={"file": "请重新保存后上传。"}) from None
        shared = [_xml_text(item) for item in shared_root.findall(f"{{{MAIN_NS}}}si")]
    result = {}
    for sheet in workbook.findall(f"{{{MAIN_NS}}}sheets/{{{MAIN_NS}}}sheet"):
        sheet_name = sheet.attrib.get("name", "")
        target = relationship_targets.get(sheet.attrib.get(f"{{{DOC_REL_NS}}}id"), "")
        if not target:
            continue
        path = posixpath.normpath(target.lstrip("/") if target.startswith("/") else posixpath.join("xl", target))
        if path not in names:
            continue
        try:
            sheet_root = ET.fromstring(archive.read(path))
        except ET.ParseError:
            raise OperationError("工作表结构损坏。", fields={"file": f"工作表“{sheet_name}”无法读取。"}) from None
        rows = []
        sheet_data = sheet_root.find(f"{{{MAIN_NS}}}sheetData")
        for fallback_row, row_node in enumerate(sheet_data.findall(f"{{{MAIN_NS}}}row") if sheet_data is not None else (), 1):
            row_number = int(row_node.attrib.get("r", fallback_row))
            cells = {}
            for cell in row_node.findall(f"{{{MAIN_NS}}}c"):
                kind = cell.attrib.get("t", "n")
                if cell.find(f"{{{MAIN_NS}}}f") is not None:
                    cells[_column_index(cell.attrib.get("r", "A1"))] = ("", "formula")
                    continue
                value_node = cell.find(f"{{{MAIN_NS}}}v")
                if kind == "inlineStr":
                    value = _xml_text(cell.find(f"{{{MAIN_NS}}}is")) if cell.find(f"{{{MAIN_NS}}}is") is not None else ""
                    textual = True
                elif value_node is None or value_node.text is None:
                    value, textual = "", kind in ("s", "str")
                elif kind == "s":
                    try:
                        value = shared[int(value_node.text)]
                    except (ValueError, IndexError):
                        value = ""
                    textual = True
                else:
                    value, textual = value_node.text, kind in ("str",)
                cells[_column_index(cell.attrib.get("r", "A1"))] = (value, "text" if textual else kind)
            rows.append((row_number, cells))
        result[sheet_name] = rows
    return result


def _split_aliases(value):
    return list(dict.fromkeys(part.strip() for part in re.split(r"[；;]", value) if part.strip()))


def _text(value):
    return str(value or "").strip()


def _sheet_rows(sheet_name, records, headers, errors):
    rows = records.get(sheet_name)
    if rows is None or not rows:
        errors.append({"sheet": sheet_name, "row": 1, "field": "表头", "message": "缺少此工作表或表头。"})
        return []
    header_values = [_text(rows[0][1].get(index, ("", ""))[0]) for index in range(len(headers))]
    header_cells = rows[0][1]
    extras = any(_text(value) for index, (value, kind) in header_cells.items() if index >= len(headers))
    if tuple(header_values) != headers or extras:
        errors.append({"sheet": sheet_name, "row": 1, "field": "表头", "message": "列名或列顺序与标准模板不一致。"})
        return []
    parsed = []
    for row_number, cells in rows[1:]:
        if not any(_text(value) for value, kind in cells.values()):
            continue
        if any(_text(value) for index, (value, kind) in cells.items() if index >= len(headers)):
            errors.append({"sheet": sheet_name, "row": row_number, "field": "额外列", "message": "请删除模板以外的数据列。"})
            continue
        if any(kind == "formula" for value, kind in cells.values()):
            errors.append({"sheet": sheet_name, "row": row_number, "field": "数据", "message": "不接受公式，请粘贴为固定值。"})
            continue
        parsed.append(
            {
                "row_number": row_number,
                "cells": cells,
                "values": [_text(cells.get(index, ("", ""))[0]) for index in range(len(headers))],
            }
        )
    return parsed


def _row_error(errors, sheet, row, field, message):
    errors.append({"sheet": sheet, "row": row, "field": field, "message": message})


def _opening_cost(value, sheet, row, field, errors):
    if not value:
        _row_error(errors, sheet, row, field, "请填写单位成本，未知成本不能留空。")
        return None
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        _row_error(errors, sheet, row, field, "请输入有效的非负金额。")
        return None
    if not parsed.is_finite() or parsed < 0 or max(0, -parsed.as_tuple().exponent) > 3:
        _row_error(errors, sheet, row, field, "金额须为非负数且最多3位小数。")
        return None
    try:
        normalized = amount(parsed, field)
    except OperationError:
        _row_error(errors, sheet, row, field, "金额超出允许范围。")
        return None
    return normalized


def _parse_workbook(content):
    records = _read_xlsx(content)
    errors = []
    if PRODUCT_SHEET not in records or STOCK_SHEET not in records:
        errors.append({"sheet": "工作簿", "row": 1, "field": "工作表", "message": "必须同时包含商品档案和期初库存两个工作表。"})
    if set(records) - {PRODUCT_SHEET, STOCK_SHEET}:
        errors.append({"sheet": "工作簿", "row": 1, "field": "工作表", "message": "不接受模板以外的工作表。"})
    product_rows = _sheet_rows(PRODUCT_SHEET, records, PRODUCT_HEADERS, errors)
    stock_rows = _sheet_rows(STOCK_SHEET, records, STOCK_HEADERS, errors)
    products = []
    seen_codes = set()
    product_shared = {}
    for row in product_rows:
        v = row["values"]
        row_number = row["row_number"]
        code, brand, type_label, series, name, aliases, language, official, box_spec, inner_box_spec, pack_spec, pack_type, unit, barcode, sku_aliases = v
        checks = (
            (code, "商品内部编号"), (brand, "IP品牌"), (type_label, "商品类型"), (name, "产品名"), (unit, "售卖单位"),
        )
        for value, field in checks:
            if not value:
                _row_error(errors, PRODUCT_SHEET, row_number, field, "此项必填。")
        if code:
            cell_type = row["cells"].get(0, ("", ""))[1]
            if cell_type != "text":
                _row_error(errors, PRODUCT_SHEET, row_number, "商品内部编号", "编号必须以文本保存。")
            if code in seen_codes:
                _row_error(errors, PRODUCT_SHEET, row_number, "商品内部编号", "编号在工作簿中重复。")
            seen_codes.add(code)
        if barcode and row["cells"].get(13, ("", ""))[1] != "text":
            _row_error(errors, PRODUCT_SHEET, row_number, "包装条码", "条码必须以文本保存以保留前导零。")
        if type_label not in PRODUCT_TYPES:
            _row_error(errors, PRODUCT_SHEET, row_number, "商品类型", "请选择补充包、周边或礼盒·预组。")
        if type_label in ("补充包", "礼盒·预组") and not language:
            _row_error(errors, PRODUCT_SHEET, row_number, "语言版本", "补充包及礼盒·预组必须填写语言版本。")
        if pack_type not in PACK_TYPES:
            _row_error(errors, PRODUCT_SHEET, row_number, "包类型", "请选择瘦包、肥包、空或留空。")
        identity = [brand, PRODUCT_TYPES.get(type_label, type_label), series, name, language, official]
        product_key = canonical_hash(identity)[:20]
        shared = _split_aliases(aliases)
        if product_key in product_shared and product_shared[product_key]["aliases"] != shared:
            _row_error(errors, PRODUCT_SHEET, row_number, "产品别名", "同一产品的公共别名不一致。")
        product_shared[product_key] = {"identity": identity, "aliases": shared, "first_row": product_shared.get(product_key, {}).get("first_row", row_number)}
        products.append(
            {
                "row_number": row_number,
                "code": code,
                "brand_name": brand,
                "type": PRODUCT_TYPES.get(type_label, ""),
                "series": series,
                "name": name,
                "product_key": product_key,
                "product_aliases": shared,
                "language": language,
                "official_code": official,
                "box_spec": box_spec,
                "inner_box_spec": inner_box_spec,
                "pack_spec": pack_spec,
                "pack_type": PACK_TYPES.get(pack_type, ""),
                "unit": unit,
                "barcode": barcode,
                "sku_aliases": _split_aliases(sku_aliases),
            }
        )
    stock = []
    seen_stock = set()
    warehouse_addresses = {}
    for row in stock_rows:
        v = row["values"]
        row_number = row["row_number"]
        warehouse_name, address, code, qty_raw, cost_raw, reserved_raw, hoarded_raw, hoard_reason, damaged_raw, note = v
        for value, field in ((warehouse_name, "仓库名"), (address, "仓库地址"), (code, "商品内部编号")):
            if not value:
                _row_error(errors, STOCK_SHEET, row_number, field, "此项必填。")
        if warehouse_name and address:
            if warehouse_name in warehouse_addresses and warehouse_addresses[warehouse_name] != address:
                _row_error(errors, STOCK_SHEET, row_number, "仓库地址", "同一仓库名的地址必须一致。")
            warehouse_addresses[warehouse_name] = address
        if code and code not in seen_codes:
            _row_error(errors, STOCK_SHEET, row_number, "商品内部编号", "该编号未出现在商品档案工作表。")
        parsed_quantities = {}
        for raw, field in ((qty_raw, "实际在库数量"), (reserved_raw, "客户预留数量"), (hoarded_raw, "囤货数量"), (damaged_raw, "破损数量")):
            if raw == "":
                _row_error(errors, STOCK_SHEET, row_number, field, "请填写非负整数，无数量时填写0。")
                parsed_quantities[field] = None
                continue
            try:
                parsed_quantities[field] = quantity(raw, field)
            except OperationError:
                _row_error(errors, STOCK_SHEET, row_number, field, "请输入非负整数。")
                parsed_quantities[field] = None
        cost = _opening_cost(cost_raw, STOCK_SHEET, row_number, "单位成本", errors)
        if parsed_quantities["囤货数量"] and not hoard_reason:
            _row_error(errors, STOCK_SHEET, row_number, "囤货原因", "有囤货数量时必须填写原因。")
        key = (warehouse_name, code)
        if key in seen_stock:
            _row_error(errors, STOCK_SHEET, row_number, "商品内部编号", "同一仓库＋SKU只能出现一行。")
        seen_stock.add(key)
        qty = parsed_quantities["实际在库数量"]
        reserved = parsed_quantities["客户预留数量"]
        hoarded = parsed_quantities["囤货数量"]
        damaged = parsed_quantities["破损数量"]
        if None not in (qty, reserved, hoarded, damaged) and reserved + hoarded + damaged > qty:
            _row_error(errors, STOCK_SHEET, row_number, "限制数量", "预留、囤货和破损合计不能超过在库数量。")
        line_cost = None
        if qty is not None and cost is not None:
            with localcontext() as ctx:
                ctx.prec = 50
                line_cost = (cost * qty).quantize(CENT, rounding=ROUND_HALF_UP)
            if abs(line_cost) >= MAX_MONEY:
                _row_error(errors, STOCK_SHEET, row_number, "单位成本", "数量乘单位成本超出数据库容量。")
        stock.append(
            {
                "row_number": row_number,
                "warehouse_name": warehouse_name,
                "warehouse_address": address,
                "code": code,
                "quantity": qty,
                "unit_cost": f"{cost:.3f}" if cost is not None else None,
                "cost_amount": f"{line_cost:.3f}" if line_cost is not None else None,
                "reserved_quantity": reserved,
                "hoarded_quantity": hoarded,
                "hoard_reason": hoard_reason,
                "damaged_quantity": damaged,
                "note": note,
            }
        )
    if not products:
        _row_error(errors, PRODUCT_SHEET, 2, "数据", "商品档案至少需要一条SKU记录。")
    if not stock:
        _row_error(errors, STOCK_SHEET, 2, "数据", "期初库存至少需要一条库存记录。")
    return {"products": products, "stock": stock}, errors


def _mapping_payload(mappings):
    if not isinstance(mappings, dict):
        mappings = {}
    result = {key: mappings.get(key, {}) for key in ("brand_prefixes", "products", "warehouses")}
    for key in result:
        if not isinstance(result[key], dict):
            result[key] = {}
    result["confirmed_duplicate_barcodes"] = mappings.get("confirmed_duplicate_barcodes", [])
    if not isinstance(result["confirmed_duplicate_barcodes"], list):
        raise OperationError("重复条码确认格式无效。", fields={"confirmed_duplicate_barcodes": "请提交条码列表。"})
    result["confirmed_duplicate_barcodes"] = sorted(set(str(v) for v in result["confirmed_duplicate_barcodes"]))
    return result


def _identity_key(identity):
    return canonical_hash(identity)[:20]


def _warehouse_key(name, address):
    return canonical_hash([name, address])[:20]


def _resolve_opening(rows, mappings, *, lock=False):
    errors = []
    requirements = []
    products_in_file = rows["products"]
    stock_in_file = rows["stock"]
    languages_by_name = {
        item.name: item for item in ProductLanguageOption.objects.filter(
            name__in={row["language"] for row in products_in_file if row["language"]}
        )
    }
    brands_by_name = {}
    brand_names = sorted({item["brand_name"] for item in products_in_file})
    brand_qs = Brand.objects.filter(name__in=brand_names).order_by("id")
    if lock:
        brand_qs = brand_qs.select_for_update()
    brands_by_name = {brand.name: brand for brand in brand_qs}

    brand_prefixes = {}
    for brand_name in brand_names:
        existing = brands_by_name.get(brand_name)
        raw_chosen = mappings["brand_prefixes"].get(brand_name, "")
        if raw_chosen and not isinstance(raw_chosen, str):
            errors.append({"sheet": PRODUCT_SHEET, "row": 2, "field": "IP前缀", "message": "请以文本填写IP前缀。"})
            raw_chosen = ""
        chosen = raw_chosen.strip() if isinstance(raw_chosen, str) else ""
        if existing:
            if not existing.is_active:
                errors.append({"sheet": PRODUCT_SHEET, "row": 2, "field": "IP品牌", "message": "同名IP已停用，请先在商品档案中处理。"})
            if chosen and chosen != existing.sku_prefix:
                errors.append({"sheet": PRODUCT_SHEET, "row": 2, "field": "IP前缀", "message": "所填前缀与现有IP档案不一致。"})
            brand_prefixes[brand_name] = existing.sku_prefix
        elif not chosen:
            requirements.append({"kind": "brand_prefix", "key": brand_name, "label": brand_name, "message": "新IP需要负责人明确填写SKU编号前缀。"})
        else:
            brand_prefixes[brand_name] = chosen
        if chosen and (not chosen or any(char.isspace() for char in chosen)):
            errors.append({"sheet": PRODUCT_SHEET, "row": 2, "field": "IP前缀", "message": "前缀不能为空且不能含空格。"})

    for item in products_in_file:
        prefix = brand_prefixes.get(item["brand_name"])
        suffix = item["code"][len(prefix):] if prefix and item["code"].startswith(prefix) else ""
        if not suffix.isascii() or not suffix.isdigit() or len(suffix) < 4:
            errors.append({"sheet": PRODUCT_SHEET, "row": item["row_number"], "field": "商品内部编号", "message": "编号须以所属IP前缀开头，后接至少4位数字。"})

    product_groups = {}
    for item in products_in_file:
        product_groups.setdefault(item["product_key"], item)
    identity_matches = {}
    product_bindings = {}
    for key, item in product_groups.items():
        language = languages_by_name.get(item["language"]) if item["language"] else None
        if item["language"] and language is None:
            errors.append({"sheet": PRODUCT_SHEET, "row": item["row_number"], "field": "语言版本", "message": "该语言尚未在商品语言选项中维护。"})
            continue
        if item["type"] in (Product.Type.BOOSTER, Product.Type.GIFT_SET) and language is None:
            errors.append({"sheet": PRODUCT_SHEET, "row": item["row_number"], "field": "语言版本", "message": "补充包及礼盒·预组必须填写已维护的语言。"})
            continue
        brand = brands_by_name.get(item["brand_name"])
        if not brand:
            identity_matches[key] = []
            continue
        qs = Product.objects.filter(
            brand=brand,
            type=item["type"],
            series=item["series"],
            name=item["name"],
            language=language,
            official_code=item["official_code"],
        ).order_by("id")
        if lock:
            qs = qs.select_for_update()
        matches = list(qs)
        identity_matches[key] = matches
        selected = mappings["products"].get(key)
        if selected in (None, ""):
            if len(matches) > 1:
                requirements.append(
                    {
                        "kind": "product",
                        "key": key,
                        "label": f"{item['brand_name']} / {item['name']} {item['language']}",
                        "candidates": [{"id": product.pk, "name": product.name, "aliases": product.aliases} for product in matches],
                    }
                )
                continue
            selected_product = matches[0] if matches else None
        else:
            selected_id = identifier(selected, f"mappings.products.{key}")
            selected_product = next((product for product in matches if product.pk == selected_id), None)
            if not selected_product:
                errors.append({"sheet": PRODUCT_SHEET, "row": item["row_number"], "field": "产品映射", "message": "所选产品不在当前候选中。"})
                continue
        if selected_product:
            if not selected_product.is_active:
                errors.append({"sheet": PRODUCT_SHEET, "row": item["row_number"], "field": "产品映射", "message": "所选产品已停用。"})
            if selected_product.aliases != item["product_aliases"]:
                errors.append({"sheet": PRODUCT_SHEET, "row": item["row_number"], "field": "产品别名", "message": "现有产品公共别名与工作簿不一致。"})
            product_bindings[key] = {"id": selected_product.pk, "create": False}
        else:
            product_bindings[key] = {"id": None, "create": True}

    selected_product_ids = {key: value["id"] for key, value in product_bindings.items() if value["id"]}
    sku_codes = sorted({item["code"] for item in products_in_file})
    sku_qs = SKU.objects.filter(code__in=sku_codes).order_by("id")
    if lock:
        sku_qs = sku_qs.select_for_update()
    existing_skus = {sku.code: sku for sku in sku_qs}
    sku_bindings = {}
    barcode_confirmations = set(mappings["confirmed_duplicate_barcodes"])
    duplicate_barcodes = {}
    for item in products_in_file:
        existing = existing_skus.get(item["code"])
        product_id = selected_product_ids.get(item["product_key"])
        if existing:
            if not product_id or existing.product_id != product_id:
                errors.append({"sheet": PRODUCT_SHEET, "row": item["row_number"], "field": "商品内部编号", "message": "现有编号关联的产品与工作簿映射不一致。"})
                continue
            expected = (item["box_spec"], item["inner_box_spec"], item["pack_spec"], item["pack_type"], item["unit"], item["barcode"], item["sku_aliases"])
            actual = (existing.box_spec, existing.inner_box_spec, existing.pack_spec, existing.pack_type, existing.unit, existing.barcode, existing.aliases)
            if not existing.is_active or actual != expected:
                errors.append({"sheet": PRODUCT_SHEET, "row": item["row_number"], "field": "商品内部编号", "message": "现有SKU资料与工作簿不一致，请先核对商品档案。"})
            sku_bindings[item["code"]] = {"id": existing.pk, "create": False}
        else:
            sku_bindings[item["code"]] = {"id": None, "create": True}
        if item["barcode"]:
            duplicate_barcodes.setdefault(item["barcode"], set()).add(item["code"])
    barcode_values = sorted(duplicate_barcodes)
    barcode_qs = SKU.objects.filter(barcode__in=barcode_values).order_by("id")
    if lock:
        barcode_qs = barcode_qs.select_for_update()
    barcode_records = list(barcode_qs)
    barcode_groups = {}
    for candidate in barcode_records:
        barcode_groups.setdefault(candidate.barcode, []).append(candidate)
    for barcode in set(duplicate_barcodes) | set(barcode_groups):
        rows_with_code = duplicate_barcodes.get(barcode, set())
        existing = barcode_groups.get(barcode, [])
        all_codes = rows_with_code | {entry.code for entry in existing}
        if len(all_codes) > 1:
            duplicate_barcodes[barcode] = all_codes
    duplicate_warnings = []
    for barcode, codes in sorted(duplicate_barcodes.items()):
        if len(codes) > 1:
            duplicate_warnings.append({"barcode": barcode, "sku_codes": sorted(codes)})
            if barcode not in barcode_confirmations:
                requirements.append({"kind": "duplicate_barcode", "key": barcode, "label": barcode, "sku_codes": sorted(codes)})

    warehouse_groups = {}
    for item in stock_in_file:
        key = _warehouse_key(item["warehouse_name"], item["warehouse_address"])
        group = warehouse_groups.setdefault(key, {"name": item["warehouse_name"], "address": item["warehouse_address"], "rows": []})
        group["rows"].append(item["row_number"])
    name_matches = {}
    warehouse_bindings = {}
    for key, group in warehouse_groups.items():
        same_name = Warehouse.objects.filter(name=group["name"]).order_by("id")
        if lock:
            same_name = same_name.select_for_update()
        candidates = list(same_name)
        matches = [warehouse for warehouse in candidates if warehouse.address == group["address"]]
        name_matches[key] = candidates
        selected = mappings["warehouses"].get(key)
        if not matches and candidates:
            errors.append({"sheet": STOCK_SHEET, "row": group["rows"][0], "field": "仓库地址", "message": "同名仓库已有不同地址，请先在仓库档案中核对。"})
            continue
        if selected in (None, ""):
            if len(matches) > 1:
                requirements.append(
                    {
                        "kind": "warehouse",
                        "key": key,
                        "label": group["name"],
                        "candidates": [{"id": item.pk, "name": item.name, "address": item.address} for item in matches],
                    }
                )
                continue
            chosen = matches[0] if matches else None
        else:
            selected_id = identifier(selected, f"mappings.warehouses.{key}")
            chosen = next((item for item in matches if item.pk == selected_id), None)
            if not chosen:
                errors.append({"sheet": STOCK_SHEET, "row": group["rows"][0], "field": "仓库映射", "message": "所选仓库不在当前候选中。"})
                continue
        if chosen:
            if not chosen.is_active:
                errors.append({"sheet": STOCK_SHEET, "row": group["rows"][0], "field": "仓库名", "message": "所选仓库已停用。"})
            warehouse_bindings[key] = {"id": chosen.pk, "create": False}
        else:
            warehouse_bindings[key] = {"id": None, "create": True}

    normalized_products = []
    for item in products_in_file:
        product_binding = product_bindings.get(item["product_key"], {"id": None, "create": True})
        brand = brands_by_name.get(item["brand_name"])
        normalized_products.append(
            {
                **item,
                "brand_id": brand.pk if brand else None,
                "brand_prefix": brand_prefixes.get(item["brand_name"]),
                "product_id": product_binding["id"],
                "create_product": product_binding["create"],
                "sku_id": sku_bindings.get(item["code"], {}).get("id"),
                "create_sku": sku_bindings.get(item["code"], {}).get("create", True),
            }
        )
    normalized_stock = []
    product_for_code = {item["code"]: item for item in normalized_products}
    for item in stock_in_file:
        key = _warehouse_key(item["warehouse_name"], item["warehouse_address"])
        binding = warehouse_bindings.get(key, {"id": None, "create": True})
        normalized_stock.append({**item, "warehouse_key": key, "warehouse_id": binding["id"], "create_warehouse": binding["create"], "sku_id": product_for_code.get(item["code"], {}).get("sku_id")})

    stock_keys = set()
    for item in normalized_stock:
        warehouse_key = ("existing", item["warehouse_id"]) if item["warehouse_id"] else ("new", item["warehouse_key"])
        sku_key = ("existing", item["sku_id"]) if item["sku_id"] else ("new", item["code"])
        target_key = (warehouse_key, sku_key)
        if target_key in stock_keys:
            errors.append({"sheet": STOCK_SHEET, "row": item["row_number"], "field": "商品内部编号", "message": "映射后仓库SKU重复。"})
        stock_keys.add(target_key)

    dependencies = {
        "brands": [[item.pk, item.name, item.sku_prefix, item.is_active] for item in brands_by_name.values()],
        "products": [[item.pk, item.brand_id, item.type, item.series, item.name, item.language.name if item.language_id else "", item.official_code, item.aliases, item.is_active] for matches in identity_matches.values() for item in matches],
        "skus": [[item.pk, item.code, item.product_id, item.box_spec, item.inner_box_spec, item.pack_spec, item.pack_type, item.unit, item.barcode, item.aliases, item.is_active] for item in existing_skus.values()],
        "warehouses": [[item.pk, item.name, item.address, item.is_active] for group in name_matches.values() for item in group],
        "existing_barcodes": [[item.pk, item.code, item.barcode] for item in barcode_records],
        "balance_count": InventoryBalance.objects.count(),
    }
    return {"products": normalized_products, "stock": normalized_stock, "rows": rows, "mappings": mappings, "duplicate_barcode_warnings": duplicate_warnings}, errors, requirements, dependencies


def _map_json(value, field):
    if not value:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            raise OperationError("映射字段须为JSON对象。", fields={field: "JSON格式无效。"}) from None
    if not isinstance(value, dict):
        raise OperationError("映射字段须为JSON对象。", fields={field: "JSON格式无效。"})
    return value


def preview_opening(actor, content, filename, mappings=None):
    _require(actor, "confirm_opening")
    if not str(filename).lower().endswith(".xlsx"):
        raise OperationError("期初文件必须为.xlsx。", fields={"file": "请上传标准Excel工作簿。"})
    rows, parse_errors = _parse_workbook(content)
    if parse_errors:
        raise OperationError("期初文件校验未通过。", fields={"rows": parse_errors})
    normalized_mappings = _mapping_payload(mappings or {})
    with _readonly_snapshot():
        control = BusinessControl.objects.get(pk=1)
        if control.opening_completed or not control.opening_enabled:
            raise OperationError("正式期初入口已关闭。", status=409, code="opening_closed")
        if InventoryBalance.objects.exists():
            raise OperationError("已有库存记录，不能追加或覆盖期初。", status=409, code="opening_closed")
        resolved, errors, requirements, dependencies = _resolve_opening(rows, normalized_mappings)
        if errors:
            raise OperationError("期初文件校验未通过。", fields={"rows": errors})
        if requirements:
            return {
                "ready": False,
                "errors": [],
                "mapping_requirements": requirements,
                "warnings": resolved["duplicate_barcode_warnings"],
            }
        input_hash = canonical_hash({"file_sha256": hashlib.sha256(content).hexdigest(), "data": resolved})
        token = signing.dumps(
            {
                "actor": actor.pk,
                "action": "opening",
                "sequence": control.last_sequence,
                "dependencies": canonical_hash(dependencies),
                "input_hash": input_hash,
                "data": resolved,
            },
            salt=PREVIEW_SALT,
            compress=True,
        )
        with localcontext() as ctx:
            ctx.prec = 50
            cost_total = sum((Decimal(item["cost_amount"]) for item in resolved["stock"]), Decimal(0))
        return {
            "ready": True,
            "preview_token": token,
            "sequence": control.last_sequence,
            "product_count": len({item["code"] for item in resolved["products"]}),
            "warehouse_count": len({item["warehouse_key"] for item in resolved["stock"]}),
            "stock_line_count": len(resolved["stock"]),
            "cost_total": f"{cost_total:.3f}",
            "warnings": resolved["duplicate_barcode_warnings"],
            "mapping_requirements": [],
        }


def confirm_opening(actor, operation_key, preview_token, request_id=""):
    key = operation_uuid(operation_key)
    token_data = _read_preview(preview_token, actor, "opening")
    resolved_from_preview = token_data.get("data")
    if not isinstance(resolved_from_preview, dict):
        raise OperationError("期初预览内容无效，请重新预览。", status=409, code="stale_preview")
    input_hash = canonical_hash({"action": "opening", "input": token_data.get("input_hash")})

    def write():
        with transaction.atomic():
            control = _lock_control()
            replay = _operation_replay(actor, key, "opening", input_hash)
            if replay is not None:
                return replay
            _require(actor, "confirm_opening")
            if control.opening_completed or not control.opening_enabled:
                raise OperationError("正式期初入口已关闭。", status=409, code="opening_closed")
            if control.last_sequence != token_data.get("sequence") or InventoryBalance.objects.exists():
                raise OperationError("预览后库存或业务顺序已变化，请重新核对。", status=409, code="stale_preview")
            rows = resolved_from_preview.get("rows")
            mappings = resolved_from_preview.get("mappings")
            if not isinstance(rows, dict) or not isinstance(mappings, dict):
                raise OperationError("期初预览内容无效，请重新预览。", status=409, code="stale_preview")
            resolved, errors, requirements, dependencies = _resolve_opening(rows, mappings, lock=True)
            if errors or requirements or canonical_hash(dependencies) != token_data.get("dependencies"):
                raise OperationError("期初映射或关联资料已变化，请重新预览。", status=409, code="stale_preview")
            plan = {key: value for key, value in resolved.items() if key not in ("rows", "mappings")}
            expected_plan = {key: value for key, value in resolved_from_preview.items() if key not in ("rows", "mappings")}
            if canonical_hash(plan) != canonical_hash(expected_plan):
                raise OperationError("期初映射结果已变化，请重新预览。", status=409, code="stale_preview")

            brand_by_name = {brand.name: brand for brand in Brand.objects.filter(name__in={item["brand_name"] for item in resolved["products"]}).order_by("id")}
            product_by_key = {}
            sku_by_code = {}
            for item in resolved["products"]:
                brand = brand_by_name.get(item["brand_name"])
                if not brand:
                    brand = Brand(name=item["brand_name"], sku_prefix=item["brand_prefix"])
                    brand.full_clean()
                    brand.save()
                    brand_by_name[brand.name] = brand
                if item["product_id"]:
                    product = Product.objects.get(pk=item["product_id"])
                else:
                    product = product_by_key.get(item["product_key"])
                    if not product:
                        product = Product(
                            brand=brand,
                            type=item["type"],
                            series=item["series"],
                            name=item["name"],
                            language=ProductLanguageOption.objects.get(name=item["language"]) if item["language"] else None,
                            official_code=item["official_code"],
                            aliases=item["product_aliases"],
                        )
                        product.full_clean()
                        product.save()
                        product_by_key[item["product_key"]] = product
                if item["sku_id"]:
                    sku = SKU.objects.get(pk=item["sku_id"])
                else:
                    sku = SKU(
                        product=product,
                        code=item["code"],
                        box_spec=item["box_spec"],
                        inner_box_spec=item["inner_box_spec"],
                        pack_spec=item["pack_spec"],
                        pack_type=item["pack_type"],
                        unit=item["unit"],
                        barcode=item["barcode"],
                        aliases=item["sku_aliases"],
                    )
                    sku.full_clean()
                    sku.save()
                sku_by_code[item["code"]] = sku

            warehouse_by_key = {}
            warehouse_ids = {item["warehouse_id"] for item in resolved["stock"] if item["warehouse_id"]}
            warehouse_by_id = {item.pk: item for item in Warehouse.objects.filter(pk__in=warehouse_ids).order_by("id")}
            for item in resolved["stock"]:
                if item["warehouse_id"]:
                    warehouse = warehouse_by_id[item["warehouse_id"]]
                else:
                    warehouse = warehouse_by_key.get(item["warehouse_key"])
                    if not warehouse:
                        warehouse = Warehouse(name=item["warehouse_name"], address=item["warehouse_address"])
                        warehouse.full_clean()
                        warehouse.save()
                        warehouse_by_key[item["warehouse_key"]] = warehouse
                item["resolved_warehouse_id"] = warehouse.pk
                item["resolved_sku_id"] = sku_by_code[item["code"]].pk

            target_keys = [(item["resolved_warehouse_id"], item["resolved_sku_id"]) for item in resolved["stock"]]
            if len(set(target_keys)) != len(target_keys):
                raise OperationError("映射后有重复的仓库SKU库存行。", status=409, code="mapping_conflict")

            sequence = _next_sequence(control)
            batch = OpeningBatch.objects.create(sequence=sequence, created_by=actor)
            set_operation_document_id(batch.pk)
            operation_payload = {
                "products": [
                    {key: item[key] for key in (
                        "brand_name", "brand_prefix", "type", "series", "name", "product_aliases", "language", "official_code",
                        "code", "box_spec", "inner_box_spec", "pack_spec", "pack_type", "unit", "barcode", "sku_aliases",
                    )}
                    for item in resolved["products"]
                ],
                "stock": [
                    {key: item[key] for key in (
                        "warehouse_name", "warehouse_address", "code", "quantity", "unit_cost", "reserved_quantity",
                        "hoarded_quantity", "hoard_reason", "damaged_quantity", "note",
                    )}
                    for item in resolved["stock"]
                ],
            }
            operation = BusinessOperation.objects.create(
                operation_key=key,
                sequence=sequence,
                actor=actor,
                action="opening",
                input_hash=input_hash,
                payload=operation_payload,
                opening_batch=batch,
            )
            lines = []
            for line_number, item in enumerate(resolved["stock"], 1):
                warehouse = Warehouse.objects.get(pk=item["resolved_warehouse_id"])
                sku = sku_by_code[item["code"]]
                unit_cost = Decimal(item["unit_cost"])
                cost_amount = Decimal(item["cost_amount"])
                if abs(cost_amount) >= MAX_MONEY:
                    raise OperationError("期初库存成本金额超出数据库容量。", fields={"rows": [{"sheet": STOCK_SHEET, "row": item["row_number"], "field": "单位成本", "message": "数量乘单位成本超出允许范围。"}]})
                balance = InventoryBalance.objects.create(
                    warehouse=warehouse,
                    sku=sku,
                    on_hand=item["quantity"],
                    reserved=item["reserved_quantity"],
                    hoarded=item["hoarded_quantity"],
                    damaged=item["damaged_quantity"],
                    cost_amount=cost_amount,
                )
                _check_balance_result(balance)
                line = OpeningLine.objects.create(
                    batch=batch,
                    line_number=line_number,
                    warehouse=warehouse,
                    sku=sku,
                    quantity=item["quantity"],
                    unit_cost=unit_cost,
                    cost_amount=cost_amount,
                    reserved_quantity=item["reserved_quantity"],
                    hoarded_quantity=item["hoarded_quantity"],
                    hoard_reason=item["hoard_reason"],
                    damaged_quantity=item["damaged_quantity"],
                    note=item["note"],
                )
                lines.append((line, item, balance))
                if item["reserved_quantity"]:
                    InventoryHold.objects.create(
                        balance=balance,
                        opening_line=line,
                        type=InventoryHold.Type.CUSTOMER_RESERVATION,
                        initial_quantity=item["reserved_quantity"],
                        remaining_quantity=item["reserved_quantity"],
                        created_by=actor,
                    )
                if item["hoarded_quantity"]:
                    InventoryHold.objects.create(
                        balance=balance,
                        opening_line=line,
                        type=InventoryHold.Type.HOARD,
                        initial_quantity=item["hoarded_quantity"],
                        remaining_quantity=item["hoarded_quantity"],
                        reason=item["hoard_reason"],
                        created_by=actor,
                    )
                if item["damaged_quantity"]:
                    DamagedRecord.objects.create(
                        balance=balance,
                        opening_line=line,
                        original_quantity=item["damaged_quantity"],
                        remaining_quantity=item["damaged_quantity"],
                        created_by=actor,
                    )
                InventoryMovement.objects.create(
                    operation=operation,
                    balance=balance,
                    opening_line=line,
                    event_kind="opening",
                    entry_key=f"line:{line.pk}",
                    on_hand_delta=item["quantity"],
                    reserved_delta=item["reserved_quantity"],
                    hoarded_delta=item["hoarded_quantity"],
                    damaged_delta=item["damaged_quantity"],
                    cost_delta=cost_amount,
                    cost_revision=balance.version,
                )
            control.opening_completed = True
            control.opening_enabled = False
            control.save(update_fields=("opening_completed", "opening_enabled"))
            result = {
                "document_id": batch.pk,
                "opening_batch_id": batch.pk,
                "sequence": sequence,
                "sku_count": len(resolved["products"]),
                "warehouse_count": len({item["resolved_warehouse_id"] for item in resolved["stock"]}),
                "stock_line_count": len(lines),
            }
            operation.result = result
            operation.save(update_fields=("result",))
            return result

    return _run_write("opening", actor, request_id, write)
