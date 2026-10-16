import hashlib
import json
import logging
import re
import sys
import time
import traceback
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from uuid import UUID

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

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
    ProductLanguageOption,
    Receipt,
    ReceiptImage,
    ReceiptLine,
    SKU,
    SalesOrder,
    SalesOrderLine,
    Supplier,
    Warehouse,
)
from .catalog import duplicate_barcode_candidates
from .permissions import ROLE_OWNER


logger = logging.getLogger("crm.business")
_operation_document_id = ContextVar("crm_operation_document_id", default="-")
CENT = Decimal("0.001")
MAX_MONEY = Decimal("100000000000000000")
MAX_QUANTITY = 2**63 - 1
PREVIEW_SALT = "crm.inventory-preview.v1"


class OperationError(Exception):
    def __init__(self, message, *, status=400, code="invalid_input", fields=None, details=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.fields = fields or {}
        self.details = details or {}


def canonical_hash(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def amount(value, field, *, required=True, nonnegative=True):
    if value is None or (isinstance(value, str) and not value.strip()):
        if not required:
            return None
        raise OperationError("请填写金额。", fields={field: "此项必填。"})
    if isinstance(value, bool) or isinstance(value, float):
        raise OperationError("金额必须使用十进制字符串。", fields={field: "金额格式无效。"})
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise OperationError("金额格式无效。", fields={field: "请输入有效金额。"}) from None
    if not result.is_finite():
        raise OperationError("金额必须是有限数值。", fields={field: "金额格式无效。"})
    if nonnegative and result < 0:
        raise OperationError("金额不能为负数。", fields={field: "不能小于0。"})
    try:
        with localcontext() as ctx:
            ctx.prec = 50
            result = result.quantize(CENT, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise OperationError("金额超出数据库容量。", fields={field: "金额超出允许范围。"}) from None
    if abs(result) >= MAX_MONEY:
        raise OperationError("金额超出数据库容量。", fields={field: "金额超出允许范围。"})
    return result


def quantity(value, field, *, positive=False):
    if isinstance(value, bool):
        raise OperationError("数量必须是整数。", fields={field: "请输入整数。"})
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
        result = int(value.strip())
    else:
        raise OperationError("数量必须是整数。", fields={field: "请输入非负整数。"})
    if result < (1 if positive else 0) or result > MAX_QUANTITY:
        label = "正整数" if positive else "非负整数"
        raise OperationError(f"数量必须为{label}。", fields={field: f"请输入{label}。"})
    return result


def identifier(value, field):
    if isinstance(value, bool) or isinstance(value, float):
        raise OperationError("记录编号无效。", fields={field: "编号格式无效。"})
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise OperationError("记录编号无效。", fields={field: "编号格式无效。"}) from None
    if result <= 0:
        raise OperationError("记录编号无效。", fields={field: "编号格式无效。"})
    return result


def operation_uuid(value):
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise OperationError("操作标识必须是UUID。", fields={"operation_key": "UUID格式无效。"}) from None


def _require(actor, codename):
    if not actor.is_authenticated:
        raise OperationError("请先登录。", status=401, code="authentication_required")
    if not actor.is_superuser and not actor.has_perm(f"crm.{codename}"):
        raise OperationError("没有此操作权限。", status=403, code="permission_denied")


def _error_location(exc):
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return "unknown"
    frame = frames[-1]
    return f"{frame.filename.rsplit('/', 1)[-1]}:{frame.name}:{frame.lineno}"


def set_operation_document_id(value):
    _operation_document_id.set(str(value))


def _run_write(action, actor, request_id, work):
    started = time.monotonic()
    context_token = _operation_document_id.set("-")
    try:
        result = work()
    except OperationError:
        _operation_document_id.reset(context_token)
        raise
    except Exception as exc:
        logger.error(
            "request_id=%s actor_id=%s document_id=%s action=%s result=failed duration_ms=%d exception_type=%s exception_location=%s",
            request_id or "-",
            actor.pk,
            _operation_document_id.get(),
            action,
            round((time.monotonic() - started) * 1000),
            type(exc).__name__,
            _error_location(exc),
        )
        _operation_document_id.reset(context_token)
        raise
    logger.info(
        "request_id=%s actor_id=%s document_id=%s action=%s result=success duration_ms=%d",
        request_id or "-",
        actor.pk,
        result.get("document_id", "-"),
        action,
        round((time.monotonic() - started) * 1000),
    )
    _operation_document_id.reset(context_token)
    return result


def _lock_control():
    try:
        return BusinessControl.objects.select_for_update().get(pk=1)
    except BusinessControl.DoesNotExist:
        raise RuntimeError("Business control row is missing; apply migrations.") from None


def _operation_replay(actor, key, action, input_hash):
    existing = BusinessOperation.objects.filter(operation_key=key).first()
    if existing is None:
        return None
    if existing.actor_id != actor.pk or existing.action != action or existing.input_hash != input_hash:
        raise OperationError(
            "操作标识已用于其他内容。",
            status=409,
            code="idempotency_conflict",
            fields={"operation_key": "请使用新的操作标识。"},
        )
    return existing.result


def _next_sequence(control):
    control.last_sequence += 1
    control.save(update_fields=("last_sequence",))
    return control.last_sequence


def _read_preview(token, actor, action):
    try:
        data = signing.loads(token, salt=PREVIEW_SALT)
    except (signing.BadSignature, signing.SignatureExpired, TypeError):
        raise OperationError("预览已失效，请重新预览。", status=409, code="stale_preview") from None
    if data.get("actor") != actor.pk or data.get("action") != action:
        raise OperationError("预览不属于当前操作人或业务类型。", status=409, code="stale_preview")
    return data


@contextmanager
def _readonly_snapshot():
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        yield


def _receipt_payload(data):
    if not isinstance(data, dict):
        raise OperationError("入库数据格式无效。")
    warehouse_id = identifier(data.get("warehouse_id"), "warehouse_id")
    supplier_id = identifier(data.get("supplier_id"), "supplier_id")
    rows = data.get("lines")
    if not isinstance(rows, list) or not rows:
        raise OperationError("至少填写一条入库明细。", fields={"lines": "至少填写一条明细。"})
    lines = []
    thresholds = {}
    for index, row in enumerate(rows, 1):
        prefix = f"lines[{index - 1}]"
        if not isinstance(row, dict):
            raise OperationError("入库明细格式无效。", fields={prefix: "请填写明细字段。"})
        sku_id = identifier(row.get("sku_id"), f"{prefix}.sku_id")
        qty = quantity(row.get("quantity"), f"{prefix}.quantity", positive=True)
        price = amount(row.get("purchase_price"), f"{prefix}.purchase_price")
        freight = amount(row.get("inbound_freight", "0"), f"{prefix}.inbound_freight")
        reserved = quantity(row.get("reserved_quantity", 0), f"{prefix}.reserved_quantity")
        damaged = quantity(row.get("damaged_quantity", 0), f"{prefix}.damaged_quantity")
        threshold = row.get("low_stock_threshold")
        threshold = None if threshold is None or (isinstance(threshold, str) and not threshold.strip()) else quantity(threshold, f"{prefix}.low_stock_threshold")
        if reserved + damaged > qty:
            raise OperationError("预留和破损数量不能超过实收数量。", fields={prefix: "预留＋破损不能超过实收数量。"})
        if sku_id in thresholds and thresholds[sku_id] != threshold:
            raise OperationError("同一SKU多行的低库存阈值不一致。", fields={prefix: "请统一该SKU的阈值。"})
        thresholds[sku_id] = threshold
        with localcontext() as ctx:
            ctx.prec = 50
            line_cost = amount(Decimal(qty) * price + freight, f"{prefix}.cost_amount")
        lines.append(
            {
                "line_number": index,
                "sku_id": sku_id,
                "quantity": qty,
                "purchase_price": f"{price:.3f}",
                "inbound_freight": f"{freight:.3f}",
                "cost_amount": f"{line_cost:.3f}",
                "reserved_quantity": reserved,
                "damaged_quantity": damaged,
                "low_stock_threshold": threshold,
            }
        )
    image_ids = data.get("image_ids", [])
    if not isinstance(image_ids, list) or len(image_ids) > 20:
        raise OperationError("每张入库单最多关联20张图片。", fields={"image_ids": "最多选择20张图片。"})
    image_ids = [identifier(value, "image_ids") for value in image_ids]
    if len(set(image_ids)) != len(image_ids):
        raise OperationError("图片不能重复关联。", fields={"image_ids": "图片编号重复。"})
    return {"warehouse_id": warehouse_id, "supplier_id": supplier_id, "lines": lines, "image_ids": image_ids}


def _receipt_reference_data(payload, *, lock=False):
    warehouse_qs = Warehouse.objects.filter(pk=payload["warehouse_id"]).order_by("id")
    supplier_qs = Supplier.objects.filter(pk=payload["supplier_id"]).order_by("id")
    if lock:
        warehouse_qs = warehouse_qs.select_for_update()
        supplier_qs = supplier_qs.select_for_update()
    warehouse = warehouse_qs.first()
    supplier = supplier_qs.first()
    if not warehouse or not supplier:
        raise OperationError("仓库或供应商不存在。", status=409, code="stale_preview")

    sku_ids = sorted({line["sku_id"] for line in payload["lines"]})
    base_skus = list(SKU.objects.filter(pk__in=sku_ids).order_by("id"))
    product_ids = sorted({sku.product_id for sku in base_skus})
    product_qs = Product.objects.filter(pk__in=product_ids).order_by("id")
    if lock:
        product_qs = product_qs.select_for_update()
    products = {product.pk: product for product in product_qs}
    brand_ids = sorted({product.brand_id for product in products.values()})
    from .models import Brand

    brand_qs = Brand.objects.filter(pk__in=brand_ids).order_by("id")
    if lock:
        brand_qs = brand_qs.select_for_update()
    brands = {brand.pk: brand for brand in brand_qs}
    sku_qs = SKU.objects.filter(pk__in=sku_ids).order_by("id")
    if lock:
        sku_qs = sku_qs.select_for_update()
    skus = {sku.pk: sku for sku in sku_qs}
    if len(skus) != len(sku_ids) or len(products) != len(product_ids) or len(brands) != len(brand_ids):
        raise OperationError("SKU或商品资料不存在。", status=409, code="stale_preview")
    for sku in skus.values():
        if not sku.is_active or not products[sku.product_id].is_active or not brands[products[sku.product_id].brand_id].is_active:
            raise OperationError("入库关联的SKU、商品或IP已停用。", status=409, code="inactive_reference")

    image_ids = payload["image_ids"]
    image_qs = ReceiptImage.objects.filter(pk__in=image_ids).order_by("id")
    if lock:
        image_qs = image_qs.select_for_update()
    images = {image.pk: image for image in image_qs}
    if len(images) != len(image_ids):
        raise OperationError("图片已失效，请重新上传。", status=409, code="stale_preview")
    for image in images.values():
        if image.receipt_id is not None or image.payment_id is not None:
            raise OperationError("图片已关联其他入库单。", status=409, code="stale_preview")
        if image.created_at < timezone.now() - timedelta(hours=24):
            raise OperationError("暂存图片已过期，请重新上传。", status=409, code="stale_preview")

    balance_qs = InventoryBalance.objects.filter(warehouse_id=warehouse.pk, sku_id__in=sku_ids).order_by("warehouse_id", "sku_id")
    if lock:
        balance_qs = balance_qs.select_for_update()
    balances = {balance.sku_id: balance for balance in balance_qs}
    snapshot = {
        "warehouse": [warehouse.pk, warehouse.name, warehouse.address, warehouse.is_active],
        "supplier": [supplier.pk, supplier.name, supplier.is_active],
        "brands": [[brand.pk, brand.name, brand.sku_prefix, brand.is_active] for brand in brands.values()],
        "products": [
            [product.pk, product.brand_id, product.type, product.series, product.name, product.language.name if product.language_id else "", product.official_code, product.aliases, product.is_active]
            for product in products.values()
        ],
        "skus": [
            [sku.pk, sku.product_id, sku.code, sku.unit, sku.barcode, sku.low_stock_threshold, sku.low_stock_enabled, sku.is_active]
            for sku in skus.values()
        ],
        "balances": [
            [sku_id, balances[sku_id].on_hand, balances[sku_id].occupied, balances[sku_id].reserved,
             balances[sku_id].hoarded, balances[sku_id].damaged, f"{balances[sku_id].cost_amount:.3f}", balances[sku_id].version]
            if sku_id in balances else [sku_id, 0, 0, 0, 0, 0, "0.000", 0]
            for sku_id in sku_ids
        ],
        "images": [[image.pk, image.owner_id, image.image_format, image.size, image.receipt_id, image.payment_id] for image in images.values()],
    }
    return warehouse, supplier, skus, products, brands, images, balances, snapshot


def preview_receipt(actor, data):
    _require(actor, "view_inventorybalance")
    payload = _receipt_payload(data)
    with _readonly_snapshot():
        control = BusinessControl.objects.get(pk=1)
        warehouse, supplier, skus, products, brands, images, balances, dependencies = _receipt_reference_data(payload)
        for image in images.values():
            if image.owner_id != actor.pk:
                raise OperationError("图片不属于当前操作人。", status=403, code="permission_denied")
        if not warehouse.is_active or not supplier.is_active:
            raise OperationError("请选择启用中的仓库和供应商。", status=409, code="inactive_reference")
        normalized = payload
        input_hash = canonical_hash(normalized)
        token = signing.dumps(
            {
                "actor": actor.pk,
                "action": "receipt",
                "sequence": control.last_sequence,
                "dependencies": canonical_hash(dependencies),
                "input_hash": input_hash,
                "payload": normalized,
            },
            salt=PREVIEW_SALT,
            compress=True,
        )
        summaries = []
        for line in payload["lines"]:
            sku = skus[line["sku_id"]]
            product = products[sku.product_id]
            brand = brands[product.brand_id]
            summaries.append(
                {
                    "line_number": line["line_number"],
                    "sku_id": sku.pk,
                    "code": sku.code,
                    "product": f"{brand.name} / {product.name} {product.language.name if product.language_id else ''}",
                    "unit": sku.unit,
                    "quantity": line["quantity"],
                    "purchase_price": line["purchase_price"],
                    "inbound_freight": line["inbound_freight"],
                    "cost_amount": line["cost_amount"],
                    "reserved_quantity": line["reserved_quantity"],
                    "damaged_quantity": line["damaged_quantity"],
                }
            )
        zero_price = any(Decimal(line["purchase_price"]) == 0 for line in payload["lines"])
        return {
            "preview_token": token,
            "sequence": control.last_sequence,
            "warehouse": {"id": warehouse.pk, "name": warehouse.name},
            "supplier": {"id": supplier.pk, "name": supplier.name},
            "lines": summaries,
            "line_count": len(summaries),
            "total_cost": f"{sum((Decimal(line['cost_amount']) for line in payload['lines']), Decimal(0)):.3f}",
            "warnings": ["存在零采购价，请核对。"] if zero_price else [],
        }


def confirm_receipt(actor, operation_key, preview_token, request_id=""):
    key = operation_uuid(operation_key)
    token_data = _read_preview(preview_token, actor, "receipt")
    payload = token_data["payload"]
    if canonical_hash(payload) != token_data.get("input_hash"):
        raise OperationError("预览内容校验失败，请重新预览。", status=409, code="stale_preview")
    input_hash = canonical_hash({"action": "receipt", "input": token_data["input_hash"]})

    def write():
        with transaction.atomic():
            control = _lock_control()
            replay = _operation_replay(actor, key, "receipt", input_hash)
            if replay is not None:
                return replay
            _require(actor, "confirm_receipt")
            if control.last_sequence != token_data["sequence"]:
                raise OperationError("预览后库存或业务配置已变化，请重新预览。", status=409, code="stale_preview")
            warehouse, supplier, skus, products, brands, images, balances, dependencies = _receipt_reference_data(payload, lock=True)
            if canonical_hash(dependencies) != token_data["dependencies"]:
                raise OperationError("预览依赖资料已变化，请重新预览。", status=409, code="stale_preview")
            if not warehouse.is_active or not supplier.is_active:
                raise OperationError("请选择启用中的仓库和供应商。", status=409, code="inactive_reference")
            for image in images.values():
                if image.owner_id != actor.pk:
                    raise OperationError("图片不属于当前操作人。", status=403, code="permission_denied")

            # Missing balance rows are safe to create under the single business-control lock.
            for sku_id in sorted(skus):
                if sku_id not in balances:
                    balances[sku_id] = InventoryBalance.objects.create(warehouse=warehouse, sku=skus[sku_id])
            totals = {}
            for line in payload["lines"]:
                item = totals.setdefault(line["sku_id"], {"quantity": 0, "reserved": 0, "damaged": 0, "cost": Decimal(0)})
                item["quantity"] += line["quantity"]
                item["reserved"] += line["reserved_quantity"]
                item["damaged"] += line["damaged_quantity"]
                item["cost"] += Decimal(line["cost_amount"])
            for sku_id, change in totals.items():
                balance = balances[sku_id]
                _check_balance_result(
                    balance,
                    on_hand=balance.on_hand + change["quantity"],
                    reserved=balance.reserved + change["reserved"],
                    damaged=balance.damaged + change["damaged"],
                    cost=balance.cost_amount + change["cost"],
                )

            sequence = _next_sequence(control)
            receipt = Receipt.objects.create(
                warehouse=warehouse,
                supplier=supplier,
                sequence=sequence,
                created_by=actor,
            )
            set_operation_document_id(receipt.pk)
            operation = BusinessOperation.objects.create(
                operation_key=key,
                sequence=sequence,
                actor=actor,
                action="receipt",
                input_hash=input_hash,
                payload=payload,
                receipt=receipt,
            )
            line_records = []
            for data_row in payload["lines"]:
                line = ReceiptLine.objects.create(
                    receipt=receipt,
                    line_number=data_row["line_number"],
                    sku=skus[data_row["sku_id"]],
                    quantity=data_row["quantity"],
                    purchase_price=Decimal(data_row["purchase_price"]),
                    inbound_freight=Decimal(data_row["inbound_freight"]),
                    cost_amount=Decimal(data_row["cost_amount"]),
                    reserved_quantity=data_row["reserved_quantity"],
                    damaged_quantity=data_row["damaged_quantity"],
                    low_stock_threshold=data_row["low_stock_threshold"],
                )
                line_records.append((line, data_row))
                balance = balances[line.sku_id]
                if line.reserved_quantity:
                    InventoryHold.objects.create(
                        balance=balance,
                        receipt_line=line,
                        type=InventoryHold.Type.CUSTOMER_RESERVATION,
                        initial_quantity=line.reserved_quantity,
                        remaining_quantity=line.reserved_quantity,
                        created_by=actor,
                    )
                if line.damaged_quantity:
                    DamagedRecord.objects.create(
                        balance=balance,
                        receipt_line=line,
                        original_quantity=line.damaged_quantity,
                        remaining_quantity=line.damaged_quantity,
                        created_by=actor,
                    )

            for sku_id, change in totals.items():
                balance = balances[sku_id]
                balance.on_hand += change["quantity"]
                balance.reserved += change["reserved"]
                balance.damaged += change["damaged"]
                balance.cost_amount += change["cost"]
                balance.version += 1
                balance.quantity_version += 1
                balance.save(update_fields=("on_hand", "reserved", "damaged", "cost_amount", "version", "quantity_version"))
            for line, data_row in line_records:
                InventoryMovement.objects.create(
                    operation=operation,
                    balance=balances[line.sku_id],
                    receipt_line=line,
                    event_kind="receipt",
                    entry_key=f"line:{line.pk}",
                    on_hand_delta=line.quantity,
                    reserved_delta=line.reserved_quantity,
                    damaged_delta=line.damaged_quantity,
                    cost_delta=line.cost_amount,
                    cost_revision=balances[line.sku_id].version,
                )
            for sku_id, threshold in {line["sku_id"]: line["low_stock_threshold"] for line in payload["lines"]}.items():
                sku = skus[sku_id]
                sku.low_stock_threshold = threshold
                sku.save(update_fields=("low_stock_threshold",))
            if images:
                bound = ReceiptImage.objects.filter(pk__in=images, payment__isnull=True, receipt__isnull=True).update(receipt=receipt)
                if bound != len(images):
                    raise OperationError("图片关联状态已变化，请重新上传。", status=409, code="stale_preview")
            result = {
                "document_id": receipt.pk,
                "receipt_id": receipt.pk,
                "sequence": sequence,
                "line_count": len(line_records),
                "total_cost": f"{sum((Decimal(row['cost_amount']) for row in payload['lines']), Decimal(0)):.3f}",
            }
            operation.result = result
            operation.save(update_fields=("result",))
            return result

    return _run_write("receipt", actor, request_id, write)


def _check_balance_result(balance, *, on_hand=None, occupied=None, reserved=None, hoarded=None, damaged=None, cost=None):
    values = {
        "on_hand": balance.on_hand if on_hand is None else on_hand,
        "occupied": balance.occupied if occupied is None else occupied,
        "reserved": balance.reserved if reserved is None else reserved,
        "hoarded": balance.hoarded if hoarded is None else hoarded,
        "damaged": balance.damaged if damaged is None else damaged,
        "cost": balance.cost_amount if cost is None else cost,
    }
    if any(values[key] < 0 or values[key] > MAX_QUANTITY for key in ("on_hand", "occupied", "reserved", "hoarded", "damaged")):
        raise OperationError("库存结果超出可保存范围。", status=409, code="stock_conflict")
    if values["occupied"] + values["reserved"] + values["hoarded"] + values["damaged"] > values["on_hand"]:
        raise OperationError("受限数量不能超过在库数量。", status=409, code="stock_conflict")
    amount(values["cost"], "cost", nonnegative=True)
    if values["on_hand"] == 0 and values["cost"] != 0:
        raise OperationError("在库为0时库存成本必须为0。", status=409, code="stock_conflict")


def create_hold(actor, data, operation_key, request_id=""):
    if not isinstance(data, dict):
        raise OperationError("限制库存数据格式无效。")
    normalized = {
        "warehouse_id": identifier(data.get("warehouse_id"), "warehouse_id"),
        "sku_id": identifier(data.get("sku_id"), "sku_id"),
        "type": data.get("type"),
        "quantity": quantity(data.get("quantity"), "quantity", positive=True),
        "reason": str(data.get("reason") or "").strip(),
    }
    if normalized["type"] not in InventoryHold.Type.values:
        raise OperationError("限制类型无效。", fields={"type": "请选择客户预留或囤货。"})
    if normalized["type"] == InventoryHold.Type.HOARD and not normalized["reason"]:
        raise OperationError("囤货必须填写原因。", fields={"reason": "此项必填。"})
    key = operation_uuid(operation_key)
    input_hash = canonical_hash({"action": "hold_create", "input": normalized})

    def write():
        with transaction.atomic():
            control = _lock_control()
            replay = _operation_replay(actor, key, "hold_create", input_hash)
            if replay is not None:
                return replay
            _require(actor, "create_inventory_hold")
            warehouse = Warehouse.objects.select_for_update().filter(pk=normalized["warehouse_id"], is_active=True).first()
            sku = SKU.objects.select_for_update().filter(pk=normalized["sku_id"], is_active=True).first()
            if not warehouse or not sku:
                raise OperationError("请选择启用中的仓库和SKU。", status=409, code="inactive_reference")
            product = Product.objects.filter(pk=sku.product_id, is_active=True).first()
            if not product or not product.brand.is_active:
                raise OperationError("SKU所属商品或IP已停用。", status=409, code="inactive_reference")
            balance = InventoryBalance.objects.select_for_update().filter(warehouse=warehouse, sku=sku).first()
            if not balance:
                raise OperationError("该仓库SKU没有库存记录。", status=409, code="stock_conflict")
            available = balance.on_hand - balance.occupied - balance.reserved - balance.hoarded - balance.damaged
            if normalized["quantity"] > available:
                raise OperationError("可售库存不足。", status=409, code="stock_conflict")
            if normalized["type"] == InventoryHold.Type.CUSTOMER_RESERVATION:
                _check_balance_result(balance, reserved=balance.reserved + normalized["quantity"])
            else:
                _check_balance_result(balance, hoarded=balance.hoarded + normalized["quantity"])

            sequence = _next_sequence(control)
            hold = InventoryHold.objects.create(
                balance=balance,
                type=normalized["type"],
                initial_quantity=normalized["quantity"],
                remaining_quantity=normalized["quantity"],
                reason=normalized["reason"],
                created_by=actor,
            )
            set_operation_document_id(hold.pk)
            operation = BusinessOperation.objects.create(
                operation_key=key,
                sequence=sequence,
                actor=actor,
                action="hold_create",
                input_hash=input_hash,
                payload=normalized,
                hold=hold,
            )
            if hold.type == InventoryHold.Type.CUSTOMER_RESERVATION:
                balance.reserved += hold.remaining_quantity
                reserved_delta, hoarded_delta = hold.remaining_quantity, 0
            else:
                balance.hoarded += hold.remaining_quantity
                reserved_delta, hoarded_delta = 0, hold.remaining_quantity
            balance.version += 1
            balance.save(update_fields=("reserved", "hoarded", "version"))
            InventoryMovement.objects.create(
                operation=operation,
                balance=balance,
                hold=hold,
                event_kind="hold_create",
                entry_key=f"hold:{hold.pk}",
                reserved_delta=reserved_delta,
                hoarded_delta=hoarded_delta,
                cost_revision=balance.version,
            )
            result = {
                "document_id": hold.pk,
                "hold_id": hold.pk,
                "sequence": sequence,
                "remaining_quantity": hold.remaining_quantity,
                "version": hold.version,
            }
            operation.result = result
            operation.save(update_fields=("result",))
            return result

    return _run_write("hold_create", actor, request_id, write)


def release_hold(actor, hold_id, data, request_id=""):
    if not isinstance(data, dict):
        raise OperationError("解锁数据格式无效。")
    hold_id = identifier(hold_id, "hold_id")
    normalized = {
        "quantity": quantity(data.get("quantity"), "quantity", positive=True),
        "expected_version": quantity(data.get("expected_version"), "expected_version", positive=True),
    }
    key = operation_uuid(data.get("operation_key"))
    input_hash = canonical_hash({"action": "hold_release", "hold_id": hold_id, "input": normalized})

    def write():
        with transaction.atomic():
            control = _lock_control()
            replay = _operation_replay(actor, key, "hold_release", input_hash)
            if replay is not None:
                return replay
            _require(actor, "release_inventory_hold")
            hold = InventoryHold.objects.select_for_update().filter(pk=hold_id).first()
            if not hold:
                raise OperationError("限制记录不存在。", status=404, code="not_found")
            balance = InventoryBalance.objects.select_for_update().get(pk=hold.balance_id)
            set_operation_document_id(hold.pk)
            if hold.version != normalized["expected_version"]:
                raise OperationError("限制记录已变化，请刷新后重试。", status=409, code="version_conflict")
            if normalized["quantity"] > hold.remaining_quantity:
                raise OperationError("解锁数量超过该记录剩余数量。", status=409, code="stock_conflict")
            _check_balance_result(
                balance,
                reserved=balance.reserved - normalized["quantity"] if hold.type == InventoryHold.Type.CUSTOMER_RESERVATION else None,
                hoarded=balance.hoarded - normalized["quantity"] if hold.type == InventoryHold.Type.HOARD else None,
            )
            sequence = _next_sequence(control)
            hold.remaining_quantity -= normalized["quantity"]
            hold.version += 1
            hold.save(update_fields=("remaining_quantity", "version"))
            operation = BusinessOperation.objects.create(
                operation_key=key,
                sequence=sequence,
                actor=actor,
                action="hold_release",
                input_hash=input_hash,
                payload={"hold_id": hold.pk, **normalized},
                hold=hold,
            )
            if hold.type == InventoryHold.Type.CUSTOMER_RESERVATION:
                balance.reserved -= normalized["quantity"]
                reserved_delta, hoarded_delta = -normalized["quantity"], 0
            else:
                balance.hoarded -= normalized["quantity"]
                reserved_delta, hoarded_delta = 0, -normalized["quantity"]
            balance.version += 1
            balance.save(update_fields=("reserved", "hoarded", "version"))
            InventoryMovement.objects.create(
                operation=operation,
                balance=balance,
                hold=hold,
                event_kind="hold_release",
                entry_key=f"release:{hold.version}",
                reserved_delta=reserved_delta,
                hoarded_delta=hoarded_delta,
                cost_revision=balance.version,
            )
            result = {
                "document_id": hold.pk,
                "hold_id": hold.pk,
                "sequence": sequence,
                "remaining_quantity": hold.remaining_quantity,
                "version": hold.version,
            }
            operation.result = result
            operation.save(update_fields=("result",))
            return result

    return _run_write("hold_release", actor, request_id, write)


def update_global_low_stock(actor, data, operation_key, request_id=""):
    if not isinstance(data, dict) or not isinstance(data.get("enabled"), bool):
        raise OperationError("全局提醒开关必须为布尔值。", fields={"enabled": "请选择开启或关闭。"})
    normalized = {
        "enabled": data["enabled"],
        "threshold": quantity(data.get("threshold"), "threshold"),
    }
    key = operation_uuid(operation_key)
    input_hash = canonical_hash({"action": "global_low_stock", "input": normalized})

    def write():
        with transaction.atomic():
            control = _lock_control()
            replay = _operation_replay(actor, key, "global_low_stock", input_hash)
            if replay is not None:
                return replay
            _require(actor, "manage_global_low_stock")
            set_operation_document_id(control.pk)
            sequence = _next_sequence(control)
            control.low_stock_enabled = normalized["enabled"]
            control.default_threshold = normalized["threshold"]
            control.save(update_fields=("low_stock_enabled", "default_threshold"))
            operation = BusinessOperation.objects.create(
                operation_key=key,
                sequence=sequence,
                actor=actor,
                action="global_low_stock",
                input_hash=input_hash,
                payload=normalized,
                control=control,
            )
            result = {
                "document_id": control.pk,
                "sequence": sequence,
                "enabled": control.low_stock_enabled,
                "threshold": control.default_threshold,
            }
            operation.result = result
            operation.save(update_fields=("result",))
            return result

    return _run_write("global_low_stock", actor, request_id, write)


def _require_opening_manager(actor):
    if not actor.is_authenticated:
        _require(actor, "manage_opening")
    if actor.is_superuser:
        return
    if not actor.groups.filter(name=ROLE_OWNER).exists():
        raise OperationError("只有老板或管理员可以管理正式期初入口。", status=403, code="permission_denied")
    _require(actor, "manage_opening")


def opening_control_data(actor):
    _require_opening_manager(actor)
    control = BusinessControl.objects.get(pk=1)
    can_toggle = not control.opening_completed
    return {
        "enabled": control.opening_enabled,
        "completed": control.opening_completed,
        "version": control.last_sequence,
        "actions": {"can_enable": can_toggle and not control.opening_enabled, "can_disable": can_toggle and control.opening_enabled},
    }


def update_opening_control(actor, data, request_id=""):
    _require_opening_manager(actor)
    if not isinstance(data, dict) or set(data) != {"enabled", "expected_version", "operation_key"}:
        raise OperationError("期初管理数据格式无效。", fields={"body": "请提供enabled、expected_version和operation_key。"})
    enabled = data["enabled"]
    if not isinstance(enabled, bool):
        raise OperationError("期初开关格式无效。", fields={"enabled": "请输入布尔值。"})
    expected_version = quantity(data["expected_version"], "expected_version")
    key = operation_uuid(data["operation_key"])
    input_hash = canonical_hash({"action": "opening_control", "enabled": enabled, "expected_version": expected_version})

    def write():
        with transaction.atomic():
            control = _lock_control()
            _require_opening_manager(actor)
            replay = _operation_replay(actor, key, "opening_control", input_hash)
            if replay is not None:
                return replay
            if expected_version != control.last_sequence:
                raise OperationError("期初管理状态已变化，请刷新后重试。", status=409, code="version_conflict")
            if control.opening_completed or OpeningBatch.objects.exists():
                raise OperationError("正式期初已入账，不能再更改开关或重复导入。", status=409, code="opening_closed")
            sequence = _next_sequence(control)
            control.opening_enabled = enabled
            control.save(update_fields=("opening_enabled",))
            operation = BusinessOperation.objects.create(
                operation_key=key,
                sequence=sequence,
                actor=actor,
                action="opening_control",
                input_hash=input_hash,
                payload={"enabled": enabled, "expected_version": expected_version},
                control=control,
            )
            result = {
                "document_id": control.pk,
                "sequence": sequence,
                "enabled": control.opening_enabled,
                "completed": control.opening_completed,
            }
            operation.result = result
            operation.save(update_fields=("result",))
            return result

    return _run_write("opening_control", actor, request_id, write)


def _catalog_text(value, field, *, required=False):
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise OperationError("商品字段格式无效。", fields={field: "请输入文本。"})
    result = value.strip()
    if required and not result:
        raise OperationError("请填写商品必填字段。", fields={field: "此项必填。"})
    return result


def _catalog_aliases(value, field):
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise OperationError("别名格式无效。", fields={field: "请输入字符串列表。"})
    return list(dict.fromkeys(item.strip() for item in value if item.strip()))


def _catalog_sku_input(data, field):
    if not isinstance(data, dict):
        raise OperationError("SKU资料格式无效。", fields={field: "每行须为对象。"})
    allowed = {
        "code", "pack_type", "unit",
        "barcode", "aliases", "confirm_duplicate_barcode",
    }
    unknown = set(data) - allowed
    if unknown:
        key = next(iter(sorted(unknown)))
        raise OperationError("SKU包含不支持的字段。", fields={f"{field}.{key}": "此字段不能修改。"})
    confirmed = data.get("confirm_duplicate_barcode", False)
    if not isinstance(confirmed, bool):
        raise OperationError("重复条码确认格式无效。", fields={f"{field}.confirm_duplicate_barcode": "请输入布尔值。"})
    return {
        "code": _catalog_text(data.get("code"), f"{field}.code", required=True),
        "pack_type": _catalog_text(data.get("pack_type"), f"{field}.pack_type"),
        "unit": _catalog_text(data.get("unit"), f"{field}.unit", required=True),
        "barcode": _catalog_text(data.get("barcode"), f"{field}.barcode"),
        "aliases": _catalog_aliases(data.get("aliases", []), f"{field}.aliases"),
        "confirm_duplicate_barcode": confirmed,
    }


def _catalog_duplicate_candidates(actor, barcodes, skus):
    if not barcodes:
        return {}
    candidates = SKU.objects.filter(barcode__in=barcodes).select_related(
        "product__brand", "product__language"
    ).order_by("id").select_for_update(of=("self",))
    grouped = {}
    for sku in candidates:
        grouped.setdefault(sku.barcode, []).append({
            "id": sku.pk,
            "code": sku.code,
            "product": sku.product.name,
            "language": sku.product.language.name if sku.product.language_id else "",
            "unit": sku.unit,
        })
    pending = {}
    for index, item in enumerate(skus):
        barcode = item["barcode"]
        if not barcode:
            continue
        local = pending.setdefault(barcode, [])
        candidate_rows = grouped.get(barcode, []) + local
        if candidate_rows and not item["confirm_duplicate_barcode"]:
            visible = actor.is_superuser or all(actor.has_perm(f"crm.{name}") for name in ("view_brand", "view_product", "view_sku"))
            raise OperationError(
                "该条码已有SKU，请先核对候选并确认后重试。",
                status=409,
                code="duplicate_barcode_confirmation_required",
                fields={f"skus[{index}].barcode": "请核对重复条码候选并确认。"},
                details={"candidates": candidate_rows if visible else []},
            )
        local.append({
            "id": None,
            "code": item["code"],
            "product": item.get("product_name", ""),
            "language": item.get("product_language", ""),
            "unit": item["unit"],
        })
    return grouped


def _catalog_model_validation(instance, *, field_prefix=""):
    try:
        instance.full_clean()
    except ValidationError as exc:
        errors = exc.message_dict if hasattr(exc, "message_dict") else {"non_field_errors": exc.messages}
        if field_prefix:
            errors = {f"{field_prefix}.{name}": messages for name, messages in errors.items()}
        raise OperationError("商品资料未通过校验。", fields=errors) from None


def create_catalog_product(actor, data, operation_key, request_id=""):
    _require(actor, "add_product")
    _require(actor, "add_sku")
    if not isinstance(data, dict):
        raise OperationError("商品资料格式无效。")
    allowed = {"brand_id", "type", "series", "name", "language_id", "official_code", "aliases", "skus"}
    unknown = set(data) - allowed
    if unknown:
        name = next(iter(sorted(unknown)))
        raise OperationError("商品包含不支持的字段。", fields={name: "此字段不能修改。"})
    sku_rows = data.get("skus")
    if not isinstance(sku_rows, list) or not sku_rows:
        raise OperationError("商品至少需要一个SKU。", fields={"skus": "请添加至少一个SKU。"})
    skus = [_catalog_sku_input(item, f"skus[{index}]") for index, item in enumerate(sku_rows)]
    brand_id = identifier(data.get("brand_id"), "brand_id")
    language_id = identifier(data.get("language_id"), "language_id") if data.get("language_id") not in (None, "") else None
    payload = {
        "brand_id": brand_id,
        "type": _catalog_text(data.get("type"), "type", required=True),
        "series": _catalog_text(data.get("series"), "series"),
        "name": _catalog_text(data.get("name"), "name", required=True),
        "language_id": language_id,
        "official_code": _catalog_text(data.get("official_code"), "official_code"),
        "aliases": _catalog_aliases(data.get("aliases", []), "aliases"),
        "skus": skus,
    }
    key = operation_uuid(operation_key)
    input_hash = canonical_hash({"action": "catalog_product_create", "input": payload})

    def write():
        with transaction.atomic():
            control = _lock_control()
            _require(actor, "add_product")
            _require(actor, "add_sku")
            replay = _operation_replay(actor, key, "catalog_product_create", input_hash)
            if replay is not None:
                return replay
            brand = Brand.objects.filter(pk=brand_id).first()
            if brand is None:
                raise OperationError("IP不存在。", status=404, code="not_found", fields={"brand_id": "请选择已有IP。"})
            product = Product(
                brand=brand,
                type=payload["type"],
                series=payload["series"],
                name=payload["name"],
                language_id=payload["language_id"],
                official_code=payload["official_code"],
                aliases=payload["aliases"],
            )
            _catalog_model_validation(product)
            for item in skus:
                item["product_name"] = product.name
                item["product_language"] = product.language.name if product.language_id else ""
            _catalog_duplicate_candidates(actor, {item["barcode"] for item in skus if item["barcode"]}, skus)
            product.save()
            saved_skus = []
            for item in skus:
                sku = SKU(product=product, **{name: value for name, value in item.items() if name not in ("confirm_duplicate_barcode", "product_name", "product_language")})
                _catalog_model_validation(sku, field_prefix=f"skus[{len(saved_skus)}]")
                try:
                    with transaction.atomic():
                        sku.save()
                except IntegrityError:
                    raise OperationError("SKU编号已被使用，请刷新候选后重试。", status=409, code="catalog_conflict", fields={f"skus[{len(saved_skus)}].code": "SKU编号冲突。"}) from None
                saved_skus.append(sku.pk)
            sequence = _next_sequence(control)
            operation = BusinessOperation.objects.create(
                operation_key=key,
                sequence=sequence,
                actor=actor,
                action="catalog_product_create",
                input_hash=input_hash,
                payload={"product_id": product.pk, "sku_ids": saved_skus},
                control=control,
            )
            result = {"product_id": product.pk, "sku_ids": saved_skus, "sequence": sequence}
            operation.result = result
            operation.save(update_fields=("result",))
            return result

    return _run_write("catalog_product_create", actor, request_id, write)


def create_catalog_sku(actor, data, operation_key, request_id=""):
    _require(actor, "add_sku")
    if not isinstance(data, dict):
        raise OperationError("SKU资料格式无效。")
    allowed = {"product_id", "code", "pack_type", "unit", "barcode", "aliases", "confirm_duplicate_barcode"}
    unknown = set(data) - allowed
    if unknown:
        name = next(iter(sorted(unknown)))
        raise OperationError("SKU包含不支持的字段。", fields={name: "此字段不能修改。"})
    product_id = identifier(data.get("product_id"), "product_id")
    sku_data = _catalog_sku_input({name: value for name, value in data.items() if name != "product_id"}, "sku")
    payload = {"product_id": product_id, **sku_data}
    key = operation_uuid(operation_key)
    input_hash = canonical_hash({"action": "catalog_sku_create", "input": payload})

    def write():
        with transaction.atomic():
            control = _lock_control()
            _require(actor, "add_sku")
            replay = _operation_replay(actor, key, "catalog_sku_create", input_hash)
            if replay is not None:
                return replay
            product = Product.objects.select_for_update(of=("self",)).select_related("language").filter(pk=product_id).first()
            if product is None:
                raise OperationError("产品不存在。", status=404, code="not_found", fields={"product_id": "请选择已有产品。"})
            row = {**sku_data, "product_name": product.name, "product_language": product.language.name if product.language_id else ""}
            _catalog_duplicate_candidates(actor, {row["barcode"]} if row["barcode"] else set(), [row])
            sku = SKU(product=product, **{name: value for name, value in sku_data.items() if name != "confirm_duplicate_barcode"})
            _catalog_model_validation(sku)
            try:
                with transaction.atomic():
                    sku.save()
            except IntegrityError:
                raise OperationError("SKU编号已被使用，请刷新候选后重试。", status=409, code="catalog_conflict", fields={"code": "SKU编号冲突。"}) from None
            sequence = _next_sequence(control)
            operation = BusinessOperation.objects.create(
                operation_key=key,
                sequence=sequence,
                actor=actor,
                action="catalog_sku_create",
                input_hash=input_hash,
                payload={"product_id": product.pk, "sku_id": sku.pk},
                control=control,
            )
            result = {"product_id": product.pk, "sku_id": sku.pk, "sequence": sequence}
            operation.result = result
            operation.save(update_fields=("result",))
            return result

    return _run_write("catalog_sku_create", actor, request_id, write)


def sku_number_suggestion(actor, brand_id):
    _require(actor, "add_product")
    brand_id = identifier(brand_id, "brand_id")
    brand = Brand.objects.filter(pk=brand_id).first()
    if brand is None:
        raise OperationError("IP不存在。", status=404, code="not_found", fields={"brand_id": "请选择已有IP。"})
    numeric_codes = []
    for code in SKU.objects.filter(product__brand=brand, code__startswith=brand.sku_prefix).values_list("code", flat=True):
        suffix = code[len(brand.sku_prefix):]
        if suffix.isascii() and suffix.isdigit():
            numeric_codes.append((int(suffix), suffix, code))
    if not numeric_codes:
        return {"current_max_code": "", "suggested_code": f"{brand.sku_prefix}0001"}
    maximum, suffix, code = max(numeric_codes)
    return {"current_max_code": code, "suggested_code": f"{brand.sku_prefix}{str(maximum + 1).zfill(max(4, len(suffix)))}"}


def update_sku_low_stock(actor, sku_id, data, operation_key, request_id=""):
    if not isinstance(data, dict) or not any(key in data for key in ("enabled", "threshold")):
        raise OperationError("请至少更新一项SKU提醒设置。")
    normalized = {}
    if "enabled" in data:
        if not isinstance(data["enabled"], bool):
            raise OperationError("SKU提醒开关必须为布尔值。", fields={"enabled": "请选择开启或关闭。"})
        normalized["enabled"] = data["enabled"]
    if "threshold" in data:
        normalized["threshold"] = None if data["threshold"] is None else quantity(data["threshold"], "threshold")
    sku_id = identifier(sku_id, "sku_id")
    key = operation_uuid(operation_key)
    input_hash = canonical_hash({"action": "sku_low_stock", "sku_id": sku_id, "input": normalized})

    def write():
        with transaction.atomic():
            control = _lock_control()
            replay = _operation_replay(actor, key, "sku_low_stock", input_hash)
            if replay is not None:
                return replay
            _require(actor, "manage_sku_low_stock")
            sku = SKU.objects.select_for_update().filter(pk=sku_id, is_active=True).first()
            if not sku:
                raise OperationError("SKU不存在或已停用。", status=404, code="not_found")
            set_operation_document_id(sku.pk)
            sequence = _next_sequence(control)
            if "enabled" in normalized:
                sku.low_stock_enabled = normalized["enabled"]
            if "threshold" in normalized:
                sku.low_stock_threshold = normalized["threshold"]
            sku.save(update_fields=tuple({"enabled": "low_stock_enabled", "threshold": "low_stock_threshold"}[key] for key in normalized))
            operation = BusinessOperation.objects.create(
                operation_key=key,
                sequence=sequence,
                actor=actor,
                action="sku_low_stock",
                input_hash=input_hash,
                payload={"sku_id": sku.pk, **normalized},
                sku=sku,
            )
            result = {
                "document_id": sku.pk,
                "sequence": sequence,
                "sku_id": sku.pk,
                "enabled": sku.low_stock_enabled,
                "threshold": sku.low_stock_threshold,
            }
            operation.result = result
            operation.save(update_fields=("result",))
            return result

    return _run_write("sku_low_stock", actor, request_id, write)


def available_quantity(balance, *, add_back=0):
    return balance.on_hand - balance.occupied - balance.reserved - balance.hoarded - balance.damaged + add_back


def inventory_rows(actor, query):
    _require(actor, "view_inventorybalance")
    control = BusinessControl.objects.get(pk=1)
    order_occupied = {}
    order_id = query.get("order_id")
    if order_id not in (None, ""):
        _require(actor, "change_salesorder")
        from .sales import sales_order_queryset_for

        order = sales_order_queryset_for(actor, changing=True).filter(pk=identifier(order_id, "order_id")).first()
        if order is None:
            raise OperationError("订单不存在或不在可维护范围。", status=404, code="not_found")
        if order.status not in (SalesOrder.Status.AWAITING_PAYMENT, SalesOrder.Status.READY_TO_SHIP):
            raise OperationError("只有未交付订单可用于修改可售量查询。", status=409, code="state_conflict")
        for warehouse_id, sku_id, quantity in order.lines.filter(active=True).values_list("warehouse_id", "sku_id", "quantity"):
            key = (warehouse_id, sku_id)
            order_occupied[key] = order_occupied.get(key, 0) + quantity
    balances = InventoryBalance.objects.select_related("warehouse", "sku__product__brand").order_by("id")
    if query.get("warehouse_id"):
        balances = balances.filter(warehouse_id=identifier(query.get("warehouse_id"), "warehouse_id"))
    if query.get("sku_id"):
        balances = balances.filter(sku_id=identifier(query.get("sku_id"), "sku_id"))
    if query.get("brand_id"):
        balances = balances.filter(sku__product__brand_id=identifier(query.get("brand_id"), "brand_id"))
    low_stock_filter = query.get("low_stock")
    if low_stock_filter not in (None, "", "true", "false"):
        raise OperationError("低库存筛选值无效。", fields={"low_stock": "请输入true或false。"})
    items = []
    for balance in balances:
        sku = balance.sku
        product = sku.product
        brand = product.brand
        available = available_quantity(balance)
        available_for_order = available_quantity(
            balance, add_back=order_occupied.get((balance.warehouse_id, balance.sku_id), 0)
        )
        threshold = sku.low_stock_threshold if sku.low_stock_threshold is not None else control.default_threshold
        alert = control.low_stock_enabled and sku.low_stock_enabled and available <= threshold
        if low_stock_filter == "true" and not alert:
            continue
        if low_stock_filter == "false" and alert:
            continue
        average = None
        if balance.on_hand:
            with localcontext() as ctx:
                ctx.prec = 50
                average = (balance.cost_amount / Decimal(balance.on_hand)).quantize(CENT, rounding=ROUND_HALF_UP)
        items.append(
            {
                "id": str(balance.pk),
                "warehouse_id": str(balance.warehouse_id),
                "warehouse": balance.warehouse.name,
                "sku_id": str(sku.pk),
                "code": sku.code,
                "product": product.name,
                "brand": brand.name,
                "unit": sku.unit,
                "on_hand": balance.on_hand,
                "occupied": balance.occupied,
                "reserved": balance.reserved,
                "hoarded": balance.hoarded,
                "damaged": balance.damaged,
                  "available": available,
                  **({"available_for_order": available_for_order} if order_id not in (None, "") else {}),
                "cost_amount": f"{balance.cost_amount:.3f}",
                "average_cost": f"{average:.3f}" if average is not None else None,
                "low_stock": alert,
                "low_stock_threshold": sku.low_stock_threshold,
                "effective_low_stock_threshold": threshold,
                "low_stock_enabled": sku.low_stock_enabled,
                "version": balance.version,
                "quantity_version": balance.quantity_version,
            }
        )
    return items


def hold_rows():
    rows = InventoryHold.objects.select_related("balance__warehouse", "balance__sku__product__brand").order_by("id")
    return [
        {
            "id": str(hold.pk),
            "warehouse_id": str(hold.balance.warehouse_id),
            "warehouse": hold.balance.warehouse.name,
            "sku_id": str(hold.balance.sku_id),
            "sku_code": hold.balance.sku.code,
            "type": hold.type,
            "type_label": hold.get_type_display(),
            "initial_quantity": hold.initial_quantity,
            "remaining_quantity": hold.remaining_quantity,
            "reason": hold.reason,
            "version": hold.version,
        }
        for hold in rows
    ]


def damaged_rows():
    rows = DamagedRecord.objects.select_related("balance__warehouse", "balance__sku__product__brand").order_by("id")
    return [
        {
            "id": str(item.pk),
            "warehouse_id": str(item.balance.warehouse_id),
            "warehouse": item.balance.warehouse.name,
            "sku_id": str(item.balance.sku_id),
            "sku_code": item.balance.sku.code,
            "original_quantity": item.original_quantity,
            "remaining_quantity": item.remaining_quantity,
            "version": item.version,
        }
        for item in rows
    ]
