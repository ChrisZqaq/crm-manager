import hashlib
import io
import re
import zipfile
from datetime import date, datetime
from decimal import Decimal

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from openpyxl import load_workbook

from .models import Brand, BusinessOperation, Product, ProductLanguageOption, SKU
from .operations import (
    PREVIEW_SALT,
    OperationError,
    _catalog_duplicate_candidates,
    _catalog_measurement,
    _catalog_model_validation,
    _catalog_release_date,
    _lock_control,
    _require,
    canonical_hash,
)


PRODUCT_SHEET = "产品导入"
SPEC_SHEET = "规格补充"
PRODUCT_HEADERS = (
    "产品标识", "IP", "商品类型", "系列", "产品名", "产品别名", "语言版本", "官方编号",
    "包型组合", "生成箱", "生成盒", "生成垃圾袋", "生成包", "其他售卖单位", "发售日期", "补充说明",
)
SPEC_HEADERS = (
    "产品标识", "包类型", "售卖单位", "规格区分", "SKU编号", "规格别名", "包装条码",
    "包装长mm", "包装宽mm", "包装高mm", "包装毛重g",
)
PRODUCT_TYPES = {
    "补充包": Product.Type.BOOSTER,
    "周边": Product.Type.MERCHANDISE,
    "礼盒·预组": Product.Type.GIFT_SET,
}
PACK_COMBINATIONS = {
    "不区分": ("",),
    "仅肥包": (SKU.PackType.FAT,),
    "仅瘦包": (SKU.PackType.SLIM,),
    "肥包和瘦包": (SKU.PackType.FAT, SKU.PackType.SLIM),
}
PACK_TYPES = {"不区分": "", "肥包": SKU.PackType.FAT, "瘦包": SKU.PackType.SLIM}
UNIT_COLUMNS = (("生成箱", "箱"), ("生成盒", "盒"), ("生成垃圾袋", "垃圾袋"), ("生成包", "包"))
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_FILES = 200
MAX_PRODUCTS = 5000
MAX_SPEC_ROWS = 15000
MAX_SKUS = 20000
MAX_PREVIEW_TOKEN_BYTES = 2_000_000


def _row_error(errors, sheet, row, field, message):
    errors.append({"sheet": sheet, "row": row, "field": field, "message": message})


def _check_xlsx(content, filename):
    if not str(filename).lower().endswith(".xlsx"):
        raise OperationError("商品导入文件必须为.xlsx。", fields={"file": "请上传标准Excel工作簿。"})
    if len(content) > MAX_FILE_BYTES:
        raise OperationError("Excel文件超出大小限制。", fields={"file": "文件不能超过10MiB。"})
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(entries) > MAX_ARCHIVE_FILES or len(set(names)) != len(names):
                raise ValueError
            if sum(entry.file_size for entry in entries) > MAX_ARCHIVE_BYTES:
                raise ValueError
            if any(entry.flag_bits & 1 for entry in entries):
                raise ValueError
            if any(name.startswith("xl/media/") or name.endswith("vbaProject.bin") for name in names):
                raise OperationError("商品导入不支持内嵌图片或宏。", fields={"file": "请使用未嵌图的.xlsx模板。"})
            if "xl/workbook.xml" not in names:
                raise ValueError
    except OperationError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile):
        raise OperationError("文件不是可读取的Excel工作簿。", fields={"file": "请上传未损坏的.xlsx文件。"}) from None


def _cell_value(cell):
    value = cell.value
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _sheet_rows(worksheet, sheet_name, headers, max_rows, errors, *, required):
    if worksheet is None:
        if required:
            _row_error(errors, "工作簿", 1, "工作表", f"缺少“{sheet_name}”工作表。")
        return []
    if worksheet.max_row and worksheet.max_row > max_rows + 1:
        _row_error(errors, sheet_name, max_rows + 2, "数据", f"最多允许{max_rows}行数据。")
        return []
    if worksheet.max_column and worksheet.max_column > len(headers):
        _row_error(errors, sheet_name, 1, "表头", "存在模板以外的列。")
        return []
    rows = []
    header_valid = False
    for row_number, cells in enumerate(worksheet.iter_rows(min_row=1, max_col=len(headers) + 1), 1):
        if row_number > max_rows + 1:
            _row_error(errors, sheet_name, row_number, "数据", f"最多允许{max_rows}行数据。")
            break
        values = [_cell_value(cell) for cell in cells[:len(headers)]]
        extra = cells[len(headers)]
        if row_number == 1:
            if any(cell.data_type == "f" for cell in cells):
                _row_error(errors, sheet_name, 1, "表头", "表头不能包含公式。")
            elif values != list(headers) or extra.value not in (None, ""):
                _row_error(errors, sheet_name, 1, "表头", "请使用模板原表头且不要增删列。")
            else:
                header_valid = True
            continue
        if all(value is None or (isinstance(value, str) and not value.strip()) for value in values) and extra.value in (None, ""):
            continue
        formulas = [headers[index] for index, cell in enumerate(cells[:len(headers)]) if cell.data_type == "f"]
        if extra.data_type == "f":
            formulas.append("模板以外的列")
        if formulas:
            for field in formulas:
                _row_error(errors, sheet_name, row_number, field, "导入表不能包含公式。")
            continue
        if extra.value not in (None, ""):
            _row_error(errors, sheet_name, row_number, "列", "存在模板以外的内容。")
            continue
        rows.append((row_number, values))
    return rows if header_valid else []


def _text(value, sheet, row, field, errors, *, required=False):
    if value is None:
        result = ""
    elif isinstance(value, str):
        result = value.strip()
    else:
        _row_error(errors, sheet, row, field, "请以文本填写。")
        return ""
    if required and not result:
        _row_error(errors, sheet, row, field, "此项必填。")
    return result


def _list_text(value, sheet, row, field, errors, *, deduplicate=True):
    text = _text(value, sheet, row, field, errors)
    if not text:
        return []
    values = [item.strip() for item in re.split(r"[;；]", text)]
    if any(not item for item in values):
        _row_error(errors, sheet, row, field, "请用分号分隔非空内容。")
        return []
    return list(dict.fromkeys(values)) if deduplicate else values


def _measurement(value, sheet, row, field, errors):
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        _row_error(errors, sheet, row, field, "请输入正数或留空。")
        return None
    try:
        parsed = _catalog_measurement(str(value), field)
    except OperationError as exc:
        _row_error(errors, sheet, row, field, str(exc))
        return None
    return format(parsed, ".3f")


def _append_model_errors(errors, sheet, row, exc):
    for field, messages in exc.fields.items():
        if not isinstance(messages, (list, tuple)):
            messages = [messages]
        for message in messages:
            _row_error(errors, sheet, row, field, str(message))


def _base_skus(product, pack_types, units):
    result = []
    for pack_type in pack_types:
        for unit in units:
            result.append({
                "sku_key": "",
                "code": "",
                "pack_type": pack_type,
                "unit": unit,
                "barcode": "",
                "aliases": [],
                "package_length_mm": None,
                "package_width_mm": None,
                "package_height_mm": None,
                "package_weight_g": None,
                "source_sheet": PRODUCT_SHEET,
                "source_row": product["source_row"],
            })
    product["skus"] = result
    return {(sku["pack_type"], sku["unit"], ""): sku for sku in result}


def _parse_rows(main_rows, spec_rows, errors):
    brands = {item.name: item for item in Brand.objects.filter(is_active=True).order_by("id")}
    languages = {item.name: item for item in ProductLanguageOption.objects.order_by("id")}
    products = []
    by_identifier = {}
    sku_maps = {}
    for row_number, values in main_rows:
        before = len(errors)
        identifier = _text(values[0], PRODUCT_SHEET, row_number, PRODUCT_HEADERS[0], errors, required=True)
        if len(identifier) > 64:
            _row_error(errors, PRODUCT_SHEET, row_number, PRODUCT_HEADERS[0], "产品标识最多64个字符。")
        if identifier in by_identifier and identifier:
            _row_error(errors, PRODUCT_SHEET, row_number, PRODUCT_HEADERS[0], "产品标识在本文件内重复。")
        brand_name = _text(values[1], PRODUCT_SHEET, row_number, PRODUCT_HEADERS[1], errors, required=True)
        type_label = _text(values[2], PRODUCT_SHEET, row_number, PRODUCT_HEADERS[2], errors, required=True)
        series = _text(values[3], PRODUCT_SHEET, row_number, PRODUCT_HEADERS[3], errors)
        name = _text(values[4], PRODUCT_SHEET, row_number, PRODUCT_HEADERS[4], errors, required=True)
        product_aliases = _list_text(values[5], PRODUCT_SHEET, row_number, PRODUCT_HEADERS[5], errors)
        language_name = _text(values[6], PRODUCT_SHEET, row_number, PRODUCT_HEADERS[6], errors)
        official_code = _text(values[7], PRODUCT_SHEET, row_number, PRODUCT_HEADERS[7], errors)
        combination = _text(values[8], PRODUCT_SHEET, row_number, PRODUCT_HEADERS[8], errors, required=True)
        pack_types = PACK_COMBINATIONS.get(combination)
        if pack_types is None:
            _row_error(errors, PRODUCT_SHEET, row_number, PRODUCT_HEADERS[8], "请选择不区分、仅肥包、仅瘦包或肥包和瘦包。")
            pack_types = ()
        units = []
        for index, (field, unit) in enumerate(UNIT_COLUMNS, 9):
            value = values[index]
            if value in (None, ""):
                selected = False
            elif value in ("是", "否"):
                selected = value == "是"
            else:
                _row_error(errors, PRODUCT_SHEET, row_number, field, "请选择是或否；留空按否处理。")
                selected = False
            if selected:
                units.append(unit)
        other_units = _list_text(values[13], PRODUCT_SHEET, row_number, PRODUCT_HEADERS[13], errors, deduplicate=False)
        if set(units).intersection(other_units) or len(other_units) != len(set(other_units)):
            _row_error(errors, PRODUCT_SHEET, row_number, PRODUCT_HEADERS[13], "其他售卖单位不能与上方选项重复。")
        units.extend(unit for unit in other_units if unit not in units)
        if not units:
            _row_error(errors, PRODUCT_SHEET, row_number, "售卖单位", "至少选择一种售卖单位。")
        raw_release_date = values[14]
        if isinstance(raw_release_date, date):
            raw_release_date = raw_release_date.isoformat()
        elif raw_release_date is not None and not isinstance(raw_release_date, str):
            _row_error(errors, PRODUCT_SHEET, row_number, PRODUCT_HEADERS[14], "请填写Excel日期或YYYY-MM-DD文本。")
            raw_release_date = ""
        elif isinstance(raw_release_date, str):
            raw_release_date = raw_release_date.strip()
        notes = _text(values[15], PRODUCT_SHEET, row_number, PRODUCT_HEADERS[15], errors)
        brand = brands.get(brand_name)
        if not brand:
            _row_error(errors, PRODUCT_SHEET, row_number, PRODUCT_HEADERS[1], "请选择现有且启用的IP。")
        product_type = PRODUCT_TYPES.get(type_label)
        if not product_type:
            _row_error(errors, PRODUCT_SHEET, row_number, PRODUCT_HEADERS[2], "请选择后台现有商品类型。")
        language = languages.get(language_name) if language_name else None
        if language_name and not language:
            _row_error(errors, PRODUCT_SHEET, row_number, PRODUCT_HEADERS[6], "请选择后台现有语言版本。")
        release_date = None
        try:
            release_date = _catalog_release_date(raw_release_date, "release_date")
        except OperationError as exc:
            _row_error(errors, PRODUCT_SHEET, row_number, PRODUCT_HEADERS[14], str(exc))
        if len(errors) != before or not identifier or identifier in by_identifier or not brand or not product_type:
            continue
        product = Product(
            brand=brand,
            type=product_type,
            series=series,
            name=name,
            language=language,
            official_code=official_code,
            aliases=product_aliases,
            release_date=release_date,
            supplement_notes=notes,
        )
        try:
            _catalog_model_validation(product)
        except OperationError as exc:
            _append_model_errors(errors, PRODUCT_SHEET, row_number, exc)
            continue
        item = {
            "file_id": identifier,
            "source_row": row_number,
            "brand_id": brand.pk,
            "brand_name": brand.name,
            "brand_prefix": brand.sku_prefix,
            "type": product.type,
            "series": product.series,
            "name": product.name,
            "language_id": language.pk if language else None,
            "language": language.name if language else "",
            "official_code": product.official_code,
            "aliases": product.aliases,
            "release_date": product.release_date.isoformat() if product.release_date else None,
            "supplement_notes": product.supplement_notes,
        }
        sku_maps[identifier] = _base_skus(item, pack_types, units)
        by_identifier[identifier] = item
        products.append(item)
    seen_specifications = set()
    for row_number, values in spec_rows:
        before = len(errors)
        identifier = _text(values[0], SPEC_SHEET, row_number, SPEC_HEADERS[0], errors, required=True)
        pack_label = _text(values[1], SPEC_SHEET, row_number, SPEC_HEADERS[1], errors, required=True)
        unit = _text(values[2], SPEC_SHEET, row_number, SPEC_HEADERS[2], errors, required=True)
        distinction = _text(values[3], SPEC_SHEET, row_number, SPEC_HEADERS[3], errors)
        code = _text(values[4], SPEC_SHEET, row_number, SPEC_HEADERS[4], errors)
        aliases = _list_text(values[5], SPEC_SHEET, row_number, SPEC_HEADERS[5], errors)
        barcode = _text(values[6], SPEC_SHEET, row_number, SPEC_HEADERS[6], errors)
        pack_type = PACK_TYPES.get(pack_label)
        if pack_type is None:
            _row_error(errors, SPEC_SHEET, row_number, SPEC_HEADERS[1], "请选择不区分、肥包或瘦包。")
        product = by_identifier.get(identifier)
        if not product:
            _row_error(errors, SPEC_SHEET, row_number, SPEC_HEADERS[0], "产品标识未出现在产品导入工作表。")
        dimensions = [
            _measurement(values[index], SPEC_SHEET, row_number, SPEC_HEADERS[index], errors)
            for index in range(7, 11)
        ]
        if code and not isinstance(values[4], str):
            _row_error(errors, SPEC_SHEET, row_number, SPEC_HEADERS[4], "SKU编号必须以文本保存。")
        if barcode and not isinstance(values[6], str):
            _row_error(errors, SPEC_SHEET, row_number, SPEC_HEADERS[6], "条码必须以文本保存以保留前导零。")
        identity = (identifier, pack_type, unit, distinction)
        if identity in seen_specifications:
            _row_error(errors, SPEC_SHEET, row_number, "规格区分", "产品、包类型、售卖单位和规格区分不能重复。")
        seen_specifications.add(identity)
        if product and pack_type is not None:
            target = sku_maps[identifier].get((pack_type, unit, ""))
            if target is None:
                _row_error(errors, SPEC_SHEET, row_number, SPEC_HEADERS[2], "该包类型和售卖单位未在产品导入表中选择。")
            elif len(errors) == before:
                nonempty = bool(distinction or code or aliases or barcode or any(value is not None for value in dimensions))
                if not nonempty:
                    _row_error(errors, SPEC_SHEET, row_number, "数据", "空规格区分行请至少填写一项补充资料。")
                else:
                    target = target if not distinction else None
                    if distinction:
                        target = {
                            "sku_key": "",
                            "code": "",
                            "pack_type": pack_type,
                            "unit": unit,
                            "barcode": "",
                            "aliases": [],
                            "package_length_mm": None,
                            "package_width_mm": None,
                            "package_height_mm": None,
                            "package_weight_g": None,
                            "source_sheet": SPEC_SHEET,
                            "source_row": row_number,
                        }
                        product["skus"].append(target)
                        sku_maps[identifier][(pack_type, unit, distinction)] = target
                    target["code"] = code
                    target["barcode"] = barcode
                    target["aliases"] = list(dict.fromkeys(([distinction] if distinction else []) + aliases))
                    for field, value in zip(("package_length_mm", "package_width_mm", "package_height_mm", "package_weight_g"), dimensions):
                        target[field] = value
        if len(errors) != before:
            continue

    total_skus = sum(len(product["skus"]) for product in products)
    if total_skus > MAX_SKUS:
        _row_error(errors, SPEC_SHEET, 1, "数据", f"本次最多生成{MAX_SKUS}个SKU。")
    return products


def _numeric_max(prefix, codes):
    values = []
    for code in codes:
        if code.startswith(prefix):
            suffix = code[len(prefix):]
            if suffix.isascii() and suffix.isdigit():
                values.append((int(suffix), suffix, code))
    return max(values) if values else None


def _assign_codes(products, errors):
    prefixes = {product["brand_id"]: product["brand_prefix"] for product in products}
    existing_by_brand = {brand_id: [] for brand_id in prefixes}
    for brand_id, code in SKU.objects.filter(product__brand_id__in=prefixes).values_list("product__brand_id", "code"):
        existing_by_brand[brand_id].append(code)
    sku_rows = []
    provided = set()
    generated = set()
    for product in products:
        for sku in product["skus"]:
            sku_rows.append((product, sku))
            if sku["code"]:
                if sku["code"] in provided:
                    _row_error(errors, sku["source_sheet"], sku["source_row"], "SKU编号", "SKU编号在本文件内重复。")
                provided.add(sku["code"])
    if provided:
        existing = set(SKU.objects.filter(code__in=provided).values_list("code", flat=True))
        for product, sku in sku_rows:
            if sku["code"] in existing:
                _row_error(errors, sku["source_sheet"], sku["source_row"], "SKU编号", f"SKU编号 {sku['code']} 已被使用，不能覆盖。")
    maxima = {}
    code_snapshot = {}
    for brand_id, prefix in prefixes.items():
        existing_codes = existing_by_brand[brand_id]
        current = _numeric_max(prefix, existing_codes)
        code_snapshot[str(brand_id)] = current[2] if current else ""
        manual_codes = [sku["code"] for product, sku in sku_rows if product["brand_id"] == brand_id and sku["code"]]
        maxima[brand_id] = _numeric_max(prefix, existing_codes + manual_codes)
    for product, sku in sku_rows:
        if sku["code"]:
            continue
        prefix = product["brand_prefix"]
        current = maxima[product["brand_id"]]
        number = current[0] + 1 if current else 1
        width = max(4, len(current[1]) if current else 4)
        code = f"{prefix}{number:0{width}d}"
        used = provided
        existing_codes = existing_by_brand[product["brand_id"]]
        while code in used or code in existing_codes:
            number += 1
            code = f"{prefix}{number:0{width}d}"
        sku["code"] = code
        provided.add(code)
        generated.add(code)
        maxima[product["brand_id"]] = (number, str(number).zfill(width), code)
    existing = set(SKU.objects.filter(code__in=generated).values_list("code", flat=True)) if generated else set()
    for product, sku in sku_rows:
        if sku["code"] in existing:
            _row_error(errors, sku["source_sheet"], sku["source_row"], "SKU编号", f"SKU编号 {sku['code']} 已被使用，不能覆盖。")
    return code_snapshot


def _sku_validation_rows(products):
    result = []
    for product in products:
        for sku in product["skus"]:
            result.append({
                **sku,
                "confirm_duplicate_barcode": True,
                "product_name": product["name"],
                "product_language": product["language"],
            })
    return result


def _identity(product):
    return (
        product["brand_id"], product["type"], product["series"], product["name"],
        product["language_id"], product["official_code"],
    )


def _candidate_data(products, actor, existing_barcode_candidates):
    identities = {_identity(product) for product in products}
    existing_products = Product.objects.filter(brand_id__in={item[0] for item in identities}).select_related("language").order_by("id")
    grouped_products = {}
    for item in existing_products:
        key = (item.brand_id, item.type, item.series, item.name, item.language_id, item.official_code)
        if key in identities:
            grouped_products.setdefault(key, []).append({
                "id": item.pk,
                "name": item.name,
                "series": item.series,
                "language": item.language.name if item.language_id else "",
                "official_code": item.official_code,
            })
    can_view_products = actor.is_superuser or actor.has_perms(("crm.view_brand", "crm.view_product"))
    snapshots = {}
    warnings = []
    local_groups = {}
    for product in products:
        key = _identity(product)
        candidates = grouped_products.get(key, [])
        snapshots[product["file_id"]] = [candidate["id"] for candidate in candidates]
        duplicate_imports = local_groups.setdefault(key, [])
        if candidates or duplicate_imports:
            warning = {
                "product_identifier": product["file_id"],
                "candidate_count": len(candidates) if can_view_products else None,
                "candidates": candidates if can_view_products else [],
                "same_file_identifiers": list(duplicate_imports),
            }
            warnings.append(warning)
        duplicate_imports.append(product["file_id"])

    visible_barcodes = actor.is_superuser or actor.has_perms(("crm.view_brand", "crm.view_product", "crm.view_sku"))
    local = {}
    barcode_warnings = []
    duplicate_sku_keys = []
    database_snapshots = {}
    for row in _sku_validation_rows(products):
        sku_key = row["sku_key"]
        barcode = row["barcode"]
        if not barcode:
            continue
        candidates = list(existing_barcode_candidates.get(barcode, [])) + list(local.get(barcode, []))
        if candidates:
            duplicate_sku_keys.append(sku_key)
            barcode_warnings.append({
                "sku_key": sku_key,
                "barcode": barcode,
                "candidate_count": len(candidates) if visible_barcodes else None,
                "candidates": candidates if visible_barcodes else [],
            })
        local.setdefault(barcode, []).append({
            "id": None,
            "sku_key": sku_key,
            "code": row["code"],
            "product": row["product_name"],
            "language": row["product_language"],
            "unit": row["unit"],
        })
        if barcode in existing_barcode_candidates:
            database_snapshots[barcode] = existing_barcode_candidates[barcode]
    return snapshots, warnings, database_snapshots, barcode_warnings, duplicate_sku_keys


def _preview_products(products):
    result = []
    for product in products:
        result.append({
            **{key: value for key, value in product.items() if key != "skus"},
            "type_label": Product(type=product["type"]).get_type_display(),
            "skus": [
                {
                    **sku,
                    "pack_type_label": SKU(pack_type=sku["pack_type"]).get_pack_type_display() or "不区分",
                }
                for sku in product["skus"]
            ],
        })
    return result


def _source_hash(products):
    rows = []
    for product in products:
        rows.append({
            **{key: value for key, value in product.items() if key not in ("source_row", "brand_prefix", "skus")},
            "skus": [
                {key: value for key, value in sku.items() if key not in ("sku_key", "source_sheet", "source_row")}
                for sku in product["skus"]
            ],
        })
    return canonical_hash(rows)


def preview_catalog_import(actor, content, filename):
    _require(actor, "add_product")
    _require(actor, "add_sku")
    _check_xlsx(content, filename)
    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=False)
    except Exception:
        raise OperationError("文件不是可读取的Excel工作簿。", fields={"file": "请上传未损坏的.xlsx文件。"}) from None
    errors = []
    try:
        unexpected = set(workbook.sheetnames) - {PRODUCT_SHEET, SPEC_SHEET, "使用说明", "填写示例"}
        for sheet in sorted(unexpected):
            _row_error(errors, "工作簿", 1, "工作表", f"不接受模板以外的工作表“{sheet}”。")
        main_rows = _sheet_rows(workbook[PRODUCT_SHEET] if PRODUCT_SHEET in workbook.sheetnames else None, PRODUCT_SHEET, PRODUCT_HEADERS, MAX_PRODUCTS, errors, required=True)
        spec_rows = _sheet_rows(workbook[SPEC_SHEET] if SPEC_SHEET in workbook.sheetnames else None, SPEC_SHEET, SPEC_HEADERS, MAX_SPEC_ROWS, errors, required=False)
    finally:
        workbook.close()
    if errors:
        return {"ready": False, "products": [], "errors": errors, "product_warnings": [], "barcode_warnings": []}

    with transaction.atomic():
        control = _lock_control()
        products = _parse_rows(main_rows, spec_rows, errors)
        if not main_rows:
            _row_error(errors, PRODUCT_SHEET, 2, "数据", "至少导入一个产品。")
        if products:
            source_hash = _source_hash(products)
            if not errors:
                previous = BusinessOperation.objects.filter(action="catalog_import", payload__source_hash=source_hash).order_by("id").first()
                if previous:
                    result = previous.result if previous.actor_id == actor.pk else None
                    return {
                        "ready": False,
                        "already_imported": True,
                        "message": "该工作簿内容已导入，未再次创建。",
                        "result": result,
                        "errors": [],
                    }
            sku_number = 0
            for product in products:
                for sku in product["skus"]:
                    sku_number += 1
                    sku["sku_key"] = f"SKU{sku_number:06d}"
            brand_ids = {product["brand_id"] for product in products}
            code_snapshot = _assign_codes(products, errors)
            brands = {item.pk: item for item in Brand.objects.filter(pk__in=brand_ids)}
            language_ids = {product["language_id"] for product in products if product["language_id"]}
            languages = {item.pk: item for item in ProductLanguageOption.objects.filter(pk__in=language_ids)}
            for product in products:
                candidate = Product(
                    brand=brands[product["brand_id"]],
                    type=product["type"],
                    series=product["series"],
                    name=product["name"],
                    language=languages.get(product["language_id"]),
                    official_code=product["official_code"],
                    aliases=product["aliases"],
                    release_date=date.fromisoformat(product["release_date"]) if product["release_date"] else None,
                    supplement_notes=product["supplement_notes"],
                )
                try:
                    _catalog_model_validation(candidate)
                except OperationError as exc:
                    _append_model_errors(errors, PRODUCT_SHEET, product["source_row"], exc)
                for sku in product["skus"]:
                    candidate.pk = -1
                    candidate_sku = SKU(
                        product=candidate,
                        code=sku["code"],
                        pack_type=sku["pack_type"],
                        unit=sku["unit"],
                        barcode=sku["barcode"],
                        aliases=sku["aliases"],
                        **{
                            field: Decimal(value) if value is not None else None
                            for field, value in sku.items()
                            if field in ("package_length_mm", "package_width_mm", "package_height_mm", "package_weight_g")
                        },
                    )
                    try:
                        candidate_sku.full_clean(exclude=("product",))
                    except ValidationError as exc:
                        fields = exc.message_dict if hasattr(exc, "message_dict") else {"non_field_errors": exc.messages}
                        _append_model_errors(errors, sku["source_sheet"], sku["source_row"], OperationError("商品资料未通过校验。", fields=fields))
            if not errors:
                sku_rows = _sku_validation_rows(products)
                barcodes = {row["barcode"] for row in sku_rows if row["barcode"]}
                existing_barcodes = _catalog_duplicate_candidates(actor, barcodes, sku_rows)
                product_candidates, product_warnings, barcode_snapshot, barcode_warnings, duplicate_sku_keys = _candidate_data(
                    products, actor, existing_barcodes
                )
                data = {
                    "products": products,
                    "product_candidate_hash": canonical_hash(product_candidates),
                    "barcode_candidate_hash": canonical_hash(barcode_snapshot),
                    "duplicate_sku_keys": duplicate_sku_keys,
                    "source_hash": source_hash,
                }
                data["code_snapshot_hash"] = canonical_hash(code_snapshot)
                file_sha256 = hashlib.sha256(content).hexdigest()
                data["file_sha256"] = file_sha256
                input_hash = canonical_hash({"file_sha256": file_sha256, "data": data})
                token = signing.dumps(
                    {"actor": actor.pk, "action": "catalog_import", "sequence": control.last_sequence, "input_hash": input_hash, "data": data},
                    salt=PREVIEW_SALT,
                    compress=True,
                )
                if len(token.encode("utf-8")) > MAX_PREVIEW_TOKEN_BYTES:
                    _row_error(errors, "工作簿", 1, "数据", "本次预览展开过大，请拆分文件后导入。")
                else:
                    return {
                        "ready": True,
                        "preview_token": token,
                        "sequence": control.last_sequence,
                        "product_count": len(products),
                        "sku_count": sum(len(product["skus"]) for product in products),
                        "products": _preview_products(products),
                        "product_warnings": product_warnings,
                        "barcode_warnings": barcode_warnings,
                        "errors": [],
                    }
    return {
        "ready": False,
        "products": _preview_products(products) if "products" in locals() else [],
        "errors": errors,
        "product_warnings": [],
        "barcode_warnings": [],
    }
