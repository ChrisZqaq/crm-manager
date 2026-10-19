from copy import deepcopy
from decimal import Decimal

from django.core import signing
from django.db import transaction
from django.utils import timezone

from .inventory_replay import (
    COUNT_COST_MISSING,
    InventoryReplay,
    effective_opening_input,
    effective_receipt_input,
    run_and_validate,
)
from .models import (
    BusinessOperation,
    BusinessControl,
    CommissionReview,
    CostCorrection,
    DamagedRecord,
    DamageResolution,
    GiftLine,
    InventoryBalance,
    InventoryHold,
    InventoryMovement,
    OpeningBatch,
    OpeningLine,
    PaymentRecord,
    Receipt,
    ReceiptLine,
    SalesOrder,
    SalesOrderLine,
    SKU,
    StockCountLine,
    SplitRecord,
    Warehouse,
)
from .operations import (
    OperationError,
    _lock_control,
    _next_sequence,
    _operation_replay,
    _readonly_snapshot,
    _require,
    _run_write,
    amount,
    canonical_hash,
    identifier,
    operation_uuid,
    quantity,
)
from .permissions import ROLE_OWNER
from .sales import sales_order_commission_visible_to, sales_order_queryset_for


PREVIEW_SALT = "crm.cost-correction.preview.v1"
PREVIEW_MAX_AGE = 600


def _source(kind, source_id, *, lock=False):
    if kind == "receipt":
        queryset = Receipt.objects
        if lock:
            queryset = queryset.select_for_update()
        document = queryset.filter(pk=source_id).first()
        if document is None:
            raise OperationError("采购入库单不存在。", status=404, code="not_found")
        operation = BusinessOperation.objects.filter(receipt_id=source_id, action="receipt").first()
        current = effective_receipt_input(document)
        version = document.version
    else:
        queryset = OpeningBatch.objects
        if lock:
            queryset = queryset.select_for_update()
        document = queryset.filter(pk=source_id).first()
        if document is None:
            raise OperationError("期初批次不存在。", status=404, code="not_found")
        operation = BusinessOperation.objects.filter(opening_batch_id=source_id, action="opening").first()
        current = effective_opening_input(document)
        version = document.version
    if operation is None or operation.sequence is None:
        raise OperationError("来源单据缺少固定业务顺序，不能纠错。", status=409, code="correction_replay_invalid")
    return document, operation, current, version


def _correction_request(data):
    if not isinstance(data, dict) or set(data) - {
        "receipt_id", "opening_batch_id", "warehouse_id", "lines", "reason", "supplemental_count_costs",
    }:
        raise OperationError("纠错数据格式无效。")
    has_receipt = data.get("receipt_id") not in (None, "")
    has_opening = data.get("opening_batch_id") not in (None, "")
    if has_receipt == has_opening:
        raise OperationError("请选择一张采购单或一个期初批次。", fields={"receipt_id": "两种来源只能选一种。"})
    kind = "receipt" if has_receipt else "opening"
    source_id = identifier(data.get("receipt_id" if has_receipt else "opening_batch_id"), "source_id")
    reason = str(data.get("reason") or "").strip()
    patches = data.get("lines")
    if not reason or not isinstance(patches, list) or not patches:
        raise OperationError("纠错原因和修正明细均为必填。", fields={"reason": "此项必填。", "lines": "至少修改一行。"})
    return {"kind": kind, "source_id": source_id, "reason": reason, "patches": patches,
            "warehouse_id": data.get("warehouse_id"), "supplemental_count_costs": data.get("supplemental_count_costs", [])}


def _normalize_correction(data, current):
    request = _correction_request(data)
    kind = request["kind"]
    proposed = deepcopy(current)
    if kind == "receipt":
        if request["warehouse_id"] not in (None, ""):
            proposed["warehouse_id"] = identifier(request["warehouse_id"], "warehouse_id")
    elif request["warehouse_id"] not in (None, ""):
        raise OperationError("期初仓库按明细更正。", fields={"warehouse_id": "请在期初明细中填写仓库。"})
    line_index = {int(item["line_number"]): item for item in proposed["lines"]}
    allowed = (
        {"sku_id", "quantity", "purchase_price", "inbound_freight", "reserved_quantity", "damaged_quantity"}
        if kind == "receipt" else
        {"warehouse_id", "sku_id", "quantity", "unit_cost", "reserved_quantity", "hoarded_quantity", "damaged_quantity"}
    )
    seen = set()
    for index, patch in enumerate(request["patches"]):
        prefix = f"lines[{index}]"
        if not isinstance(patch, dict) or "line_number" not in patch or set(patch) - ({"line_number"} | allowed):
            raise OperationError("纠错行格式无效。", fields={prefix: "请提供固定行号和允许更正的字段。"})
        line_number = quantity(patch["line_number"], f"{prefix}.line_number", positive=True)
        if line_number in seen or line_number not in line_index:
            raise OperationError("纠错行号重复或不存在。", fields={f"{prefix}.line_number": "请使用来源单据中的唯一行号。"})
        seen.add(line_number)
        row = line_index[line_number]
        for field in allowed - {"quantity", "sku_id", "warehouse_id", "purchase_price", "inbound_freight", "unit_cost", "reserved_quantity", "hoarded_quantity", "damaged_quantity"}:
            if field in patch:
                row[field] = patch[field]
        for field in ("sku_id", "warehouse_id"):
            if field in patch:
                row[field] = identifier(patch[field], f"{prefix}.{field}")
        if "quantity" in patch:
            row["quantity"] = quantity(patch["quantity"], f"{prefix}.quantity", positive=(kind == "receipt"))
        for field in ("reserved_quantity", "hoarded_quantity", "damaged_quantity"):
            if field in patch:
                row[field] = quantity(patch[field], f"{prefix}.{field}")
        for field in ("purchase_price", "inbound_freight", "unit_cost"):
            if field in patch:
                row[field] = f"{amount(patch[field], f'{prefix}.{field}'):.3f}"
        if kind == "receipt":
            row.setdefault("reserved_quantity", 0)
            row.setdefault("damaged_quantity", 0)
    if kind == "receipt":
        proposed["lines"] = sorted(proposed["lines"], key=lambda row: row["line_number"])
    else:
        proposed["lines"] = sorted(proposed["lines"], key=lambda row: row["line_number"])
    supplements = request["supplemental_count_costs"]
    if not isinstance(supplements, list):
        raise OperationError("历史盘点补充成本格式无效。", fields={"supplemental_count_costs": "请输入明细列表。"})
    normalized_supplements = []
    supplement_ids = set()
    for index, item in enumerate(supplements):
        prefix = f"supplemental_count_costs[{index}]"
        if not isinstance(item, dict) or set(item) != {"count_line_id", "unit_cost"}:
            raise OperationError("历史盘点补充成本格式无效。", fields={prefix: "每行需要count_line_id和unit_cost。"})
        count_line_id = identifier(item["count_line_id"], f"{prefix}.count_line_id")
        if count_line_id in supplement_ids:
            raise OperationError("历史盘点行不能重复补充成本。", fields={prefix: "请移除重复项。"})
        supplement_ids.add(count_line_id)
        normalized_supplements.append({"count_line_id": count_line_id, "unit_cost": f"{amount(item['unit_cost'], f'{prefix}.unit_cost'):.3f}"})
    normalized_supplements.sort(key=lambda row: row["count_line_id"])
    if canonical_hash(proposed) == canonical_hash(current) and not normalized_supplements:
        raise OperationError("纠错内容与当前有效输入相同。", fields={"lines": "请修改实际错误字段。"})
    return {"kind": kind, "id": request["source_id"], "reason": request["reason"],
            "input": proposed, "supplemental_count_costs": normalized_supplements}


def _validate_references(actor, source, current):
    kind, source_id = source["kind"], source["id"]
    old_lines = {int(row["line_number"]): row for row in current["lines"]}
    new_lines = {int(row["line_number"]): row for row in source["input"]["lines"]}
    sku_ids = {int(row["sku_id"]) for row in current["lines"]} | {int(row["sku_id"]) for row in source["input"]["lines"]}
    warehouse_ids = set()
    if kind == "receipt":
        warehouse_ids.update((int(current["warehouse_id"]), int(source["input"]["warehouse_id"])))
    else:
        warehouse_ids.update(int(row["warehouse_id"]) for row in current["lines"])
        warehouse_ids.update(int(row["warehouse_id"]) for row in source["input"]["lines"])
    skus = {row.pk: row for row in SKU.objects.select_related("product__brand").filter(pk__in=sku_ids)}
    warehouses = {row.pk: row for row in Warehouse.objects.filter(pk__in=warehouse_ids)}
    if len(skus) != len(sku_ids) or len(warehouses) != len(warehouse_ids):
        raise OperationError("纠错引用的仓库或SKU不存在。", status=409, code="inactive_reference")
    changed_skus = {
        int(row["sku_id"])
        for line_number, row in new_lines.items()
        if int(row["sku_id"]) != int(old_lines[line_number]["sku_id"])
    }
    if any(not skus[item].is_active or not skus[item].product.is_active or not skus[item].product.brand.is_active for item in changed_skus):
        raise OperationError("新SKU及所属商品必须启用。", status=409, code="inactive_reference")
    if kind == "receipt":
        changed_warehouses = {int(source["input"]["warehouse_id"])} if int(source["input"]["warehouse_id"]) != int(current["warehouse_id"]) else set()
    else:
        changed_warehouses = {
            int(row["warehouse_id"])
            for line_number, row in new_lines.items()
            if int(row["warehouse_id"]) != int(old_lines[line_number]["warehouse_id"])
        }
    if any(not warehouses[item].is_active for item in changed_warehouses):
        raise OperationError("新仓库必须启用。", status=409, code="inactive_reference")


def _supplement_map(rows):
    return {int(row["count_line_id"]): Decimal(row["unit_cost"]) for row in rows}


def _run_proposal(source, supplements):
    _, base_replay = run_and_validate()
    override = {"kind": source["kind"], "id": source["id"], **source["input"]}
    proposed_replay = InventoryReplay(override, _supplement_map(supplements))
    try:
        proposed_result = proposed_replay.run()
    except OperationError as exc:
        if exc.code == COUNT_COST_MISSING:
            return None, base_replay, proposed_replay, [int(value) for value in exc.fields.get("count_line_id", "").split("=")[-1:] if value.isdigit()]
        raise
    provided = set(_supplement_map(supplements))
    used = proposed_result.supplemented_count_lines
    if provided != used:
        raise OperationError("补充成本必须对应本次回放确实缺少历史均价的盘点行。", status=409, code="invalid_count_cost_supplement",
                             fields={"unused_count_line_ids": sorted(provided - used), "missing_count_line_ids": sorted(used - provided)})
    return proposed_result, base_replay, proposed_replay, []


def _keys_from_input(kind, value):
    if kind == "receipt":
        warehouse_ids = [int(value["warehouse_id"])]
        return {(warehouse_ids[0], int(line["sku_id"])) for line in value["lines"]}
    return {(int(line["warehouse_id"]), int(line["sku_id"])) for line in value["lines"]}


def _impact_after_source(kind, source_id, source_sequence, before, after, replay, baseline_result, proposed_result):
    touched = _keys_from_input(kind, before) | _keys_from_input(kind, after)
    impacted = {"orders": set(), "splits": set(), "shipments": set(), "gifts": set(), "counts": set(), "damage_resolutions": set()}
    operations = baseline_result.operations
    split_ops = [op for op in operations if op.sequence > source_sequence and op.action == "inventory_split"]
    expanded = True
    while expanded:
        expanded = False
        for operation in split_ops:
            record = replay.split_rows.get(operation.split_record_id)
            if not record:
                continue
            source_key = (record.source_warehouse_id, record.source_sku_id)
            if source_key in touched:
                impacted["splits"].add(record.pk)
                target_key = (record.target_warehouse_id, record.target_sku_id)
                if target_key not in touched:
                    touched.add(target_key)
                    expanded = True
    for operation in operations:
        if operation.sequence <= source_sequence:
            continue
        if operation.action in ("sales_order_submit", "sales_order_amend"):
            if any((int(line["warehouse_id"]), int(line["sku_id"])) in touched for line in operation.payload.get("lines", [])):
                impacted["orders"].add(operation.sales_order_id or int(operation.payload["order_id"]))
        elif operation.action in ("sales_order_ship", "sales_order_onsite_delivery"):
            if any((move.balance.warehouse_id, move.balance.sku_id) in touched for move in replay.moves_by_operation.get(operation.pk, []) if move.event_kind == "order_outbound"):
                impacted["orders"].add(operation.sales_order_id or int(operation.payload["order_id"]))
                impacted["shipments"].add(operation.pk)
        elif operation.action == "inventory_gift":
            for line in replay.gift_lines.values():
                if line.gift_id == operation.gift_id and (line.warehouse_id, line.sku_id) in touched:
                    impacted["gifts"].add(line.gift_id)
        elif operation.action == "stock_count_approve":
            for line in replay.count_lines.values():
                if line.stock_count_id == operation.stock_count_id and (line.warehouse_id, line.sku_id) in touched:
                    impacted["counts"].add(line.stock_count_id)
        elif operation.action == "damage_resolution":
            resolution = replay.damage_resolutions.get(operation.damage_resolution_id)
            if resolution:
                before_row = baseline_result.damage.get(resolution.damaged_record_id)
                after_row = proposed_result.damage.get(resolution.damaged_record_id)
                if (before_row and before_row["key"] in touched) or (after_row and after_row["key"] in touched):
                    impacted["damage_resolutions"].add(resolution.pk)
    return bool(any(impacted.values())), {key: sorted(value) for key, value in impacted.items()}, touched


def _movement_value(value):
    return {
        "warehouse_sku": list(value["key"]),
        "on_hand_delta": value["on_hand_delta"], "occupied_delta": value["occupied_delta"],
        "reserved_delta": value["reserved_delta"], "hoarded_delta": value["hoarded_delta"],
        "damaged_delta": value["damaged_delta"], "cost_delta": f"{value['cost_delta']:.3f}",
    }


def _result_differences(before, after):
    def format_amount(value):
        return f"{value:.3f}" if value is not None else None

    changes = {"balances": [], "order_lines": [], "orders": [], "movements": [], "splits": [], "gifts": [], "damage_resolutions": [], "counts": [], "fulfillments": []}
    for key in sorted(before.balances.keys() | after.balances.keys()):
        left, right = before.balance(key), after.balance(key)
        fields = ("on_hand", "occupied", "reserved", "hoarded", "damaged", "cost_amount")
        if any(left[field] != right[field] for field in fields):
            changes["balances"].append({
                "warehouse_id": key[0], "sku_id": key[1],
                "before": {field: f"{left[field]:.3f}" if field == "cost_amount" else left[field] for field in fields},
                "after": {field: f"{right[field]:.3f}" if field == "cost_amount" else right[field] for field in fields},
            })
    cost_line_fields = ("unit_cost_snapshot", "order_cost_amount")
    for line_id in sorted(before.order_lines.keys() | after.order_lines.keys()):
        left, right = before.order_lines.get(line_id), after.order_lines.get(line_id)
        if left is None or right is None:
            continue
        if any(left[field] != right[field] for field in cost_line_fields):
            changes["order_lines"].append({
                "line_id": line_id, "order_id": right["order_id"],
                  "before": {field: format_amount(left[field]) for field in cost_line_fields},
                  "after": {field: format_amount(right[field]) for field in cost_line_fields},
            })
    for order_id in sorted(before.orders.keys() | after.orders.keys()):
        left, right = before.orders.get(order_id), after.orders.get(order_id)
        if left is None or right is None:
            continue
        fields = ("revenue_amount", "cost_amount", "margin_amount", "commission_estimate")
        if any(left[field] != right[field] for field in fields):
            changes["orders"].append({"order_id": order_id,
                                        "before": {field: format_amount(left[field]) for field in fields},
                                        "after": {field: format_amount(right[field]) for field in fields}})
    for identity in sorted(before.movements.keys() | after.movements.keys(), key=str):
        left, right = before.movements.get(identity), after.movements.get(identity)
        if left != right:
            changes["movements"].append({"operation_id": identity[0], "event_kind": identity[1], "entry_key": identity[2],
                                         "before": _movement_value(left) if left else None,
                                         "after": _movement_value(right) if right else None})
    for field, kind, left_map, right_map in (
        ("splits", "split_id", before.split_costs, after.split_costs),
        ("gifts", "gift_line_id", before.gift_costs, after.gift_costs),
    ):
        for row_id in sorted(left_map.keys() | right_map.keys()):
            left, right = left_map.get(row_id), right_map.get(row_id)
            if left != right:
                changes[field].append({kind: row_id, "before": f"{left:.3f}" if left is not None else None, "after": f"{right:.3f}" if right is not None else None})
    for resolution_id in sorted(before.damage_costs.keys() | after.damage_costs.keys()):
        left, right = before.damage_costs.get(resolution_id), after.damage_costs.get(resolution_id)
        if left != right:
            changes["damage_resolutions"].append({"resolution_id": resolution_id,
                "before": {k: f"{v:.3f}" for k, v in left.items()} if left else None,
                "after": {k: f"{v:.3f}" for k, v in right.items()} if right else None})
    for line_id in sorted(before.count_costs.keys() | after.count_costs.keys()):
        left, right = before.count_costs.get(line_id), after.count_costs.get(line_id)
        if left != right:
            changes["counts"].append({"count_line_id": line_id,
                "before": {k: f"{v:.3f}" if isinstance(v, Decimal) else v for k, v in left.items()} if left else None,
                "after": {k: f"{v:.3f}" if isinstance(v, Decimal) else v for k, v in right.items()} if right else None})
    for operation_id in sorted(before.fulfillment_costs.keys() | after.fulfillment_costs.keys()):
        left, right = before.fulfillment_costs.get(operation_id), after.fulfillment_costs.get(operation_id)
        if left != right:
            changes["fulfillments"].append({"operation_id": operation_id,
                "before": {"quantity": left["quantity"], "cost": f"{left['cost']:.3f}"} if left else None,
                "after": {"quantity": right["quantity"], "cost": f"{right['cost']:.3f}"} if right else None})
    return {key: value for key, value in changes.items() if value}


def visible_cost_correction_data(actor, data):
    """Filter order details for a correction response without changing its saved approval facts."""
    order_ids = set()

    def collect(value, order_list=False):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ("order_id", "sales_order_id"):
                    try:
                        order_ids.add(int(child))
                    except (TypeError, ValueError):
                        pass
                collect(child, key in ("orders", "order_ids", "sales_order_ids"))
        elif isinstance(value, (list, tuple)):
            for child in value:
                if order_list and not isinstance(child, (dict, list, tuple)):
                    try:
                        order_ids.add(int(child))
                    except (TypeError, ValueError):
                        pass
                else:
                    collect(child)

    collect(data)
    orders = {
        row.pk: row
        for row in sales_order_queryset_for(actor).filter(pk__in=order_ids).only("pk", "salesperson_id", "amount_version")
    }
    commission_order_ids = {
        order_id for order_id, order in orders.items()
        if sales_order_commission_visible_to(actor, order)
    }
    commission_reviews = {
        row.order_id: row
        for row in CommissionReview.objects.filter(order_id__in=commission_order_ids).only(
            "order_id", "approved_amount", "approved_amount_version",
        )
    }
    decision_pairs = set()
    if actor.is_superuser or _owner(actor):
        decision_pairs = {
            (payload.get("cost_correction_id"), order_id)
            for order_id, payload in BusinessOperation.objects.filter(
                action="cost_correction_commission_decision", sales_order_id__in=orders,
            ).values_list("sales_order_id", "payload")
        }
    omit = object()

    def clean(value, order_context=None, order_list=False):
        if isinstance(value, dict):
            direct_id = value.get("order_id", value.get("sales_order_id", order_context))
            if direct_id is not None:
                try:
                    direct_id = int(direct_id)
                except (TypeError, ValueError):
                    direct_id = None
            if direct_id is not None and direct_id not in orders:
                return omit
            result = {}
            for key, child in value.items():
                if key == "commission_adjustment_suggestions" and not (actor.is_superuser or _owner(actor)):
                    continue
                if key == "commission_adjustment_suggestions":
                    active = []
                    for suggestion in child if isinstance(child, list) else []:
                        try:
                            suggestion_order_id = int(suggestion["order_id"])
                            correction_id = int(suggestion["cost_correction_id"])
                        except (KeyError, TypeError, ValueError):
                            continue
                        order = orders.get(suggestion_order_id)
                        review = commission_reviews.get(suggestion_order_id)
                        if (
                            order is None or review is None or review.approved_amount is None
                            or order.amount_version != suggestion.get("expected_amount_version")
                            or review.approved_amount_version != suggestion.get("expected_approved_amount_version")
                            or f"{review.approved_amount:.3f}" != suggestion.get("approved_amount")
                            or (correction_id, suggestion_order_id) in decision_pairs
                        ):
                            continue
                        active.append(suggestion)
                    if active:
                        result[key] = active
                    continue
                if key == "commission_estimate" and direct_id not in commission_order_ids:
                    continue
                is_order_list = key in ("orders", "order_ids", "sales_order_ids") and isinstance(child, (list, tuple))
                cleaned = clean(child, direct_id, is_order_list)
                if cleaned is not omit:
                    result[key] = cleaned
            return result
        if isinstance(value, (list, tuple)):
            result = []
            for child in value:
                if order_list and not isinstance(child, (dict, list, tuple)):
                    try:
                        order_id = int(child)
                    except (TypeError, ValueError):
                        continue
                    if order_id in orders:
                        result.append(child)
                else:
                    cleaned = clean(child, order_context)
                    if cleaned is not omit:
                        result.append(cleaned)
            return result
        return value

    visible = clean(data)
    return {} if visible is omit else visible


def _source_version(kind, document):
    return document.version


def preview_cost_correction(actor, data):
    _require(actor, "submit_costcorrection")
    request = _correction_request(data)
    with _readonly_snapshot():
        control = BusinessControl.objects.get(pk=1)
        document, source_operation, current, version = _source(request["kind"], request["source_id"])
        normalized = _normalize_correction(data, current)
        _validate_references(actor, normalized, current)
        supplemental = normalized["supplemental_count_costs"]
        source = {"kind": normalized["kind"], "id": normalized["id"], "input": normalized["input"]}
        proposed, base_replay, proposed_replay, missing = _run_proposal(source, supplemental)
        if missing:
            return {
                "status": "count_cost_required", "sequence": control.last_sequence,
                "source_kind": normalized["kind"], "source_id": normalized["id"],
                "missing_count_costs": [{"count_line_id": item} for item in missing],
                "message": "请补充历史盘盈成本后重新预览。",
            }
        impact_required, impact, touched = _impact_after_source(
            normalized["kind"], normalized["id"], source_operation.sequence, current, normalized["input"],
            base_replay, base_replay.result, proposed,
        )
        differences = _result_differences(base_replay.result, proposed)
        input_hash = canonical_hash({"source": source, "reason": normalized["reason"], "supplemental_count_costs": supplemental})
        token = signing.dumps({
            "actor": actor.pk, "action": "cost_correction", "sequence": control.last_sequence,
            "source_kind": normalized["kind"], "source_id": normalized["id"], "request_version": version,
            "source_input_hash": canonical_hash(current), "input": normalized["input"],
            "reason": normalized["reason"], "supplemental_count_costs": supplemental,
            "input_hash": input_hash, "impact_required": impact_required,
        }, salt=PREVIEW_SALT, compress=True)
        response = {
            "status": "ready", "sequence": control.last_sequence, "source_kind": normalized["kind"],
            "source_id": normalized["id"], "source_version": version,
            "approval_required": impact_required, "input_hash": input_hash,
            "impact": {key: value for key, value in impact.items() if value},
            "changes": differences, "preview_token": token,
        }
        return visible_cost_correction_data(actor, response)


def _load_token(token, actor):
    try:
        value = signing.loads(token, salt=PREVIEW_SALT, max_age=PREVIEW_MAX_AGE)
    except (signing.BadSignature, signing.SignatureExpired, TypeError):
        raise OperationError("纠错预览已失效，请重新预览。", status=409, code="stale_preview") from None
    if value.get("actor") != actor.pk or value.get("action") != "cost_correction":
        raise OperationError("纠错预览不属于当前操作人。", status=409, code="stale_preview")
    return value


def _source_key(kind, ident):
    return "receipt_id" if kind == "receipt" else "opening_batch_id"


def _owner(actor):
    return actor.groups.filter(name=ROLE_OWNER).exists()


def _proposal_from_token(token_data):
    return {
        "kind": token_data["source_kind"], "id": int(token_data["source_id"]),
        "input": token_data["input"], "reason": token_data["reason"],
        "supplemental_count_costs": token_data.get("supplemental_count_costs", []),
    }


def _current_preview_locked(token_data, source):
    control = BusinessControl.objects.get(pk=1)
    if control.last_sequence != token_data["sequence"]:
        raise OperationError("预览后库存或业务数据已变化，请重新预览。", status=409, code="stale_preview")
    document, source_operation, current, version = _source(source["kind"], source["id"], lock=True)
    if version != token_data["request_version"] or canonical_hash(current) != token_data["source_input_hash"]:
        raise OperationError("来源单据已变化，请重新预览。", status=409, code="version_conflict")
    _validate_references(None, source, current)
    proposed, base_replay, proposed_replay, missing = _run_proposal(source, source["supplemental_count_costs"])
    if missing:
        raise OperationError("纠错回放需要历史盘盈成本。", status=409, code=COUNT_COST_MISSING,
                             fields={"count_line_ids": missing})
    impact_required, impact, touched = _impact_after_source(
        source["kind"], source["id"], source_operation.sequence, current, source["input"],
        base_replay, base_replay.result, proposed,
    )
    if impact_required != token_data["impact_required"]:
        raise OperationError("纠错影响范围已变化，请重新预览。", status=409, code="stale_preview")
    return control, document, current, version, source_operation, base_replay, proposed


def _record_source_input(source, correction):
    proposed = source["input"]
    if source["kind"] == "receipt":
        receipt = correction.receipt
        lines = {line.line_number: line for line in receipt.lines.select_for_update().all()}
        receipt.warehouse_id = int(proposed["warehouse_id"])
        receipt.version += 1
        receipt.save(update_fields=("warehouse", "version"))
        for item in proposed["lines"]:
            line = lines[int(item["line_number"])]
            line.sku_id = int(item["sku_id"])
            line.quantity = int(item["quantity"])
            line.purchase_price = Decimal(item["purchase_price"])
            line.inbound_freight = Decimal(item["inbound_freight"])
            line.cost_amount = amount(line.purchase_price * line.quantity + line.inbound_freight, "cost_amount")
            line.reserved_quantity = int(item["reserved_quantity"])
            line.damaged_quantity = int(item["damaged_quantity"])
            line.save(update_fields=("sku", "quantity", "purchase_price", "inbound_freight", "cost_amount", "reserved_quantity", "damaged_quantity"))
    else:
        batch = correction.opening_batch
        lines = {line.line_number: line for line in batch.lines.select_for_update().all()}
        batch.version += 1
        batch.save(update_fields=("version",))
        for item in proposed["lines"]:
            line = lines[int(item["line_number"])]
            line.warehouse_id = int(item["warehouse_id"])
            line.sku_id = int(item["sku_id"])
            line.quantity = int(item["quantity"])
            line.unit_cost = Decimal(item["unit_cost"])
            line.cost_amount = amount(line.unit_cost * line.quantity, "cost_amount")
            line.reserved_quantity = int(item["reserved_quantity"])
            line.hoarded_quantity = int(item["hoarded_quantity"])
            line.damaged_quantity = int(item["damaged_quantity"])
            line.save(update_fields=("warehouse", "sku", "quantity", "unit_cost", "cost_amount", "reserved_quantity", "hoarded_quantity", "damaged_quantity"))


def _movement_changed(before, after):
    if before is None or after is None:
        return True
    return before != after


def _persist_result(result, replay, before_result, correction, operation):
    version_touched = set()
    quantity_touched = set()
    changed_movements = set()
    for identity in before_result.movements.keys() | result.movements.keys():
        old, new = before_result.movements.get(identity), result.movements.get(identity)
        if not _movement_changed(old, new):
            continue
        changed_movements.add(identity)
        if old:
            version_touched.add(old["key"])
        if new:
            version_touched.add(new["key"])
        if old and new and (old["key"] != new["key"] or old["on_hand_delta"] != new["on_hand_delta"]):
            quantity_touched.add(old["key"])
            quantity_touched.add(new["key"])
        elif old and not new and old["on_hand_delta"]:
            quantity_touched.add(old["key"])
        elif new and not old and new["on_hand_delta"]:
            quantity_touched.add(new["key"])
    for stock_key in before_result.balances.keys() | result.balances.keys():
        old, new = before_result.balance(stock_key), result.balance(stock_key)
        if old != new:
            version_touched.add(stock_key)
            if old["on_hand"] != new["on_hand"]:
                quantity_touched.add(stock_key)

    move_rows = {
        (row.operation_id, row.event_kind, row.entry_key): row
        for row in replay.movements
        if (row.operation_id, row.event_kind, row.entry_key) in changed_movements
    }
    if set(move_rows) != changed_movements or not changed_movements <= result.movements.keys():
        raise OperationError("回放流水与现存流水行不一致。", status=409, code="correction_replay_invalid")
    locked_move_ids = [row.pk for row in move_rows.values()]
    existing_moves = {
        (row.operation_id, row.event_kind, row.entry_key): row
        for row in InventoryMovement.objects.select_for_update().select_related("balance")
        .filter(pk__in=locked_move_ids).order_by("id")
    }
    affected_keys = set(version_touched)
    balance_ids = [replay.db_balances[key].pk for key in affected_keys if key in replay.db_balances]
    balance_rows = {
        (row.warehouse_id, row.sku_id): row
        for row in InventoryBalance.objects.select_for_update().filter(pk__in=balance_ids)
        .order_by("warehouse_id", "sku_id", "id")
    }
    missing_keys = affected_keys - balance_rows.keys()
    warehouses = {row.pk: row for row in Warehouse.objects.filter(pk__in={key[0] for key in missing_keys})}
    skus = {row.pk: row for row in SKU.objects.filter(pk__in={key[1] for key in missing_keys})}
    for stock_key in sorted(missing_keys):
        try:
            balance_rows[stock_key] = InventoryBalance.objects.create(
                warehouse=warehouses[stock_key[0]], sku=skus[stock_key[1]],
            )
        except KeyError:
            raise OperationError("回放目标仓库或SKU不存在。", status=409, code="correction_replay_invalid") from None

    for identity in sorted(changed_movements, key=str):
        values = result.movements[identity]
        row = existing_moves.get(identity)
        if row is None:
            raise OperationError("回放流水与现存流水行不一致。", status=409, code="correction_replay_invalid", fields={"operation_id": str(identity[0]), "entry_key": identity[2]})
        balance = balance_rows[values["key"]]
        changed = row.balance_id != balance.pk or (
            row.on_hand_delta != values["on_hand_delta"] or row.occupied_delta != values["occupied_delta"]
            or row.reserved_delta != values["reserved_delta"] or row.hoarded_delta != values["hoarded_delta"]
            or row.damaged_delta != values["damaged_delta"] or row.cost_delta != values["cost_delta"]
        )
        if changed:
            row.balance = balance
            for field in ("on_hand_delta", "occupied_delta", "reserved_delta", "hoarded_delta", "damaged_delta", "cost_delta"):
                setattr(row, field, values[f"{field}"])
            row.cost_revision += 1
            row.save(update_fields=("balance", "on_hand_delta", "occupied_delta", "reserved_delta", "hoarded_delta", "damaged_delta", "cost_delta", "cost_revision"))

    for stock_key in sorted(affected_keys):
        state = result.balance(stock_key)
        row = balance_rows[stock_key]
        row.on_hand = state["on_hand"]
        row.occupied = state["occupied"]
        row.reserved = state["reserved"]
        row.hoarded = state["hoarded"]
        row.damaged = state["damaged"]
        row.cost_amount = state["cost_amount"]
        fields = ["on_hand", "occupied", "reserved", "hoarded", "damaged", "cost_amount"]
        if stock_key in version_touched:
            row.version += 1
            fields.append("version")
        if stock_key in quantity_touched:
            row.quantity_version += 1
            fields.append("quantity_version")
        row.save(update_fields=tuple(fields))

    affected_holds = [(hold_id, state) for hold_id, state in result.holds.items() if state["key"] in affected_keys]
    changed_hold_ids = set()
    for hold_id, state in affected_holds:
        existing = replay.hold_by_id.get(hold_id) if isinstance(hold_id, int) else None
        if existing is None or any(
            getattr(existing, key) != value if key not in ("balance",) else existing.balance_id != value
            for key, value in {
                "balance": balance_rows[state["key"]].pk, "type": state["type"],
                "initial_quantity": state["initial_quantity"], "remaining_quantity": state["remaining_quantity"],
            }.items()
        ):
            if existing is not None:
                changed_hold_ids.add(existing.pk)
    locked_holds = {
        row.pk: row for row in InventoryHold.objects.select_for_update()
        .filter(pk__in=changed_hold_ids).order_by("id")
    }
    for hold_id, state in affected_holds:
        if isinstance(hold_id, int) and hold_id not in changed_hold_ids:
            continue
        existing = locked_holds.get(hold_id) if isinstance(hold_id, int) else None
        values = {
            "balance": balance_rows[state["key"]], "type": state["type"],
            "initial_quantity": state["initial_quantity"], "remaining_quantity": state["remaining_quantity"],
        }
        if existing is None:
            values["created_by_id"] = state["created_by_id"]
            if state.get("receipt_line_id"):
                values["receipt_line_id"] = state["receipt_line_id"]
            if state.get("opening_line_id"):
                values["opening_line_id"] = state["opening_line_id"]
            existing = InventoryHold.objects.create(**values)
        else:
            changed = any(getattr(existing, key) != value if key not in ("balance",) else existing.balance_id != value.pk for key, value in values.items())
            if changed:
                for key, value in values.items():
                    setattr(existing, key, value)
                existing.version += 1
                existing.save(update_fields=tuple(values) + ("version",))

    affected_damage = [(damage_id, state) for damage_id, state in result.damage.items() if state["key"] in affected_keys]
    changed_damage_ids = set()
    for damage_id, state in affected_damage:
        existing = replay.damage_by_id.get(damage_id) if isinstance(damage_id, int) else None
        if existing is None or (
            existing.balance_id != balance_rows[state["key"]].pk
            or existing.original_quantity != state["original_quantity"]
            or existing.remaining_quantity != state["remaining_quantity"]
        ):
            if existing is not None:
                changed_damage_ids.add(existing.pk)
    locked_damage = {
        row.pk: row for row in DamagedRecord.objects.select_for_update()
        .filter(pk__in=changed_damage_ids).order_by("id")
    }
    for damage_id, state in affected_damage:
        if isinstance(damage_id, int) and damage_id not in changed_damage_ids:
            continue
        existing = locked_damage.get(damage_id) if isinstance(damage_id, int) else None
        values = {"balance": balance_rows[state["key"]], "original_quantity": state["original_quantity"],
                  "remaining_quantity": state["remaining_quantity"]}
        if existing is None:
            values["created_by_id"] = state["created_by_id"]
            if state.get("receipt_line_id"):
                values["receipt_line_id"] = state["receipt_line_id"]
            if state.get("opening_line_id"):
                values["opening_line_id"] = state["opening_line_id"]
            existing = DamagedRecord.objects.create(**values)
        else:
            changed = existing.balance_id != values["balance"].pk or existing.original_quantity != values["original_quantity"] or existing.remaining_quantity != values["remaining_quantity"]
            if changed:
                for key, value in values.items():
                    setattr(existing, key, value)
                existing.version += 1
                existing.save(update_fields=("balance", "original_quantity", "remaining_quantity", "version"))

    split_changes = {
        split_id: cost for split_id, cost in result.split_costs.items()
        if replay.split_rows[split_id].transferred_cost != cost
    }
    for row in SplitRecord.objects.select_for_update().filter(pk__in=split_changes).order_by("id"):
        row.transferred_cost = split_changes[row.pk]
        row.save(update_fields=("transferred_cost",))
    gift_changes = {
        line_id: cost for line_id, cost in result.gift_costs.items()
        if replay.gift_lines[line_id].cost_amount != cost
    }
    for row in GiftLine.objects.select_for_update().filter(pk__in=gift_changes).order_by("id"):
        row.cost_amount = gift_changes[row.pk]
        row.save(update_fields=("cost_amount",))
    damage_changes = {
        resolution_id: values for resolution_id, values in result.damage_costs.items()
        if replay.damage_resolutions[resolution_id].deducted_cost != values["cost"]
        or replay.damage_resolutions[resolution_id].difference != values["difference"]
    }
    for row in DamageResolution.objects.select_for_update().filter(pk__in=damage_changes).order_by("id"):
        values = damage_changes[row.pk]
        row.deducted_cost = values["cost"]
        row.difference = values["difference"]
        row.save(update_fields=("deducted_cost", "difference"))
    count_changes = {
        line_id: values for line_id, values in result.count_costs.items()
        if replay.count_lines[line_id].delta != values["delta"]
        or replay.count_lines[line_id].cost_delta != values["cost"]
        or replay.count_lines[line_id].effective_unit_cost != values["unit_cost"]
    }
    for row in StockCountLine.objects.select_for_update().filter(pk__in=count_changes).order_by("id"):
        values = count_changes[row.pk]
        row.delta = values["delta"]
        row.effective_unit_cost = values["unit_cost"]
        row.cost_delta = values["cost"]
        row.save(update_fields=("delta", "effective_unit_cost", "cost_delta"))
    supplement_map = _supplement_map(correction.supplemental_count_costs)
    if supplement_map:
        for row in StockCountLine.objects.select_for_update().filter(pk__in=supplement_map).order_by("id"):
            unit_cost = supplement_map[row.pk]
            if row.supplemental_unit_cost != unit_cost or row.supplement_approval_id != correction.pk:
                row.supplemental_unit_cost = unit_cost
                row.supplement_approval = correction
                row.save(update_fields=("supplemental_unit_cost", "supplement_approval"))

    changed_orders = []
    line_changes = set()
    changed_line_ids = []
    for line_id, values in result.order_lines.items():
        line = replay.db_order_lines.get(line_id)
        if line is None:
            raise OperationError("回放订单明细与现存明细不一致。", status=409, code="correction_replay_invalid")
        if (
            line.unit_cost_snapshot != values["unit_cost_snapshot"]
            or line.order_cost_amount != values["order_cost_amount"]
            or line.cost_mode != values["cost_mode"]
        ):
            changed_line_ids.append(line_id)
    changed_lines = SalesOrderLine.objects.select_for_update().filter(pk__in=changed_line_ids).order_by("order_id", "id")
    for line in changed_lines:
        values = result.order_lines[line.pk]
        line_changes.add(line.order_id)
        line.unit_cost_snapshot = values["unit_cost_snapshot"]
        line.order_cost_amount = values["order_cost_amount"]
        line.cost_mode = values["cost_mode"]
        line.save(update_fields=("unit_cost_snapshot", "order_cost_amount", "cost_mode"))

    changed_order_ids = set(line_changes)
    for order_id, values in result.orders.items():
        order = replay.db_orders.get(order_id)
        if order is None:
            raise OperationError("回放订单与现存订单不一致。", status=409, code="correction_replay_invalid")
        if any(getattr(order, field) != values[field] for field in ("revenue_amount", "cost_amount", "margin_amount", "commission_estimate")):
            changed_order_ids.add(order_id)
    commission_adjustment_suggestions = []
    locked_orders = SalesOrder.objects.select_for_update().filter(pk__in=changed_order_ids).order_by("id")
    for order in locked_orders:
        order_id = order.pk
        values = result.orders[order_id]
        active_change = (
            order.revenue_amount != values["revenue_amount"] or order.cost_amount != values["cost_amount"]
            or order.margin_amount != values["margin_amount"] or order.commission_estimate != values["commission_estimate"]
        )
        if not active_change and order_id not in line_changes:
            continue
        order.revenue_amount = values["revenue_amount"]
        order.cost_amount = values["cost_amount"]
        order.margin_amount = values["margin_amount"]
        order.commission_estimate = values["commission_estimate"]
        order.version += 1
        fields = ["revenue_amount", "cost_amount", "margin_amount", "commission_estimate", "version", "updated_at"]
        amount_version_before_correction = order.amount_version
        if active_change:
            order.amount_version += 1
            fields.append("amount_version")
        order.save(update_fields=tuple(fields))
        review = CommissionReview.objects.select_for_update().filter(order=order, approved_amount__isnull=False).first()
        if review is not None and active_change and review.approved_amount_version == amount_version_before_correction:
            payment = PaymentRecord.objects.select_for_update().filter(order=order).first()
            if (
                order.status == SalesOrder.Status.COMPLETED
                and values["commission_estimate"] is not None
                and payment is not None
                and payment.status == PaymentRecord.Status.PASSED
                and not payment.paused
                and payment.expected_payment_version == order.payment_version
                and payment.verified_version == order.payment_version
            ):
                commission_adjustment_suggestions.append({
                    "cost_correction_id": correction.pk,
                    "order_id": order.pk,
                    "source_amount_version": amount_version_before_correction,
                    "expected_amount_version": order.amount_version,
                    "approved_amount": f"{review.approved_amount:.3f}",
                    "expected_approved_amount_version": review.approved_amount_version,
                    "target_amount": f"{values['commission_estimate']:.3f}",
                    "delta_amount": f"{amount(values['commission_estimate'] - review.approved_amount, 'delta', nonnegative=False):.3f}",
                })
        changed_orders.append(order_id)

    historical_operations = {row.pk: row for row in replay.operations}
    changed_operation_ids = {
        operation_id for operation_id, values in result.fulfillment_costs.items()
        if historical_operations[operation_id].result.get("actual_inventory_cost") != f"{values['cost']:.3f}"
    }
    for historical in BusinessOperation.objects.select_for_update().filter(pk__in=changed_operation_ids).order_by("id"):
        values = result.fulfillment_costs[historical.pk]
        historical.result = {**historical.result, "actual_inventory_cost": f"{values['cost']:.3f}"}
        historical.save(update_fields=("result",))
    correction.before_after = {
        **correction.before_after,
        "commission_adjustment_suggestions": commission_adjustment_suggestions,
    }
    correction.save(update_fields=("before_after",))
    return {"changed_order_ids": changed_orders}


def _supplement_map(rows):
    return {int(row["count_line_id"]): Decimal(row["unit_cost"]) for row in rows}


def _preview_from_token(token):
    return {
        "kind": token["source_kind"], "id": int(token["source_id"]), "input": token["input"],
        "reason": token["reason"], "supplemental_count_costs": token.get("supplemental_count_costs", []),
    }


def _operation_input_hash(token):
    return canonical_hash({"source": {"kind": token["source_kind"], "id": token["source_id"], "input": token["input"]},
                           "reason": token["reason"], "supplemental_count_costs": token.get("supplemental_count_costs", [])})


def submit_cost_correction(actor, operation_key, preview_token, request_id=""):
    token = _load_token(preview_token, actor)
    source = _preview_from_token(token)
    key = operation_uuid(operation_key)
    input_hash = _operation_input_hash(token)
    action = "cost_correction_direct" if token["impact_required"] is False else "cost_correction_submit"

    def write():
        with transaction.atomic():
            control = _lock_control()
            _require(actor, "submit_costcorrection")
            replayed = _operation_replay(actor, key, action, input_hash)
            if replayed is not None:
                return replayed
            if control.last_sequence != token["sequence"]:
                raise OperationError("预览后库存或业务数据已变化，请重新预览。", status=409, code="stale_preview")
            document, source_operation, current, version = _source(source["kind"], source["id"], lock=True)
            if version != token["request_version"] or canonical_hash(current) != token["source_input_hash"]:
                raise OperationError("来源单据已变化，请重新预览。", status=409, code="version_conflict")
            _validate_references(actor, source, current)
            proposed, base_replay, proposed_replay, missing = _run_proposal(source, source["supplemental_count_costs"])
            if missing:
                raise OperationError("纠错回放需要历史盘盈成本。", status=409, code=COUNT_COST_MISSING,
                                     fields={"count_line_ids": missing})
            impact_required, impact, touched = _impact_after_source(
                source["kind"], source["id"], source_operation.sequence, current, source["input"],
                base_replay, base_replay.result, proposed,
            )
            if impact_required != token["impact_required"]:
                raise OperationError("纠错影响范围已变化，请重新预览。", status=409, code="stale_preview")
            sequence = _next_sequence(control)
            correction = CostCorrection.objects.create(
                receipt=document if source["kind"] == "receipt" else None,
                opening_batch=document if source["kind"] == "opening" else None,
                status=CostCorrection.Status.PENDING if impact_required else CostCorrection.Status.APPLIED,
                reason=source["reason"], original_input=current, proposed_input=source["input"],
                supplemental_count_costs=source["supplemental_count_costs"],
                before_after={"source": {"before": current, "after": source["input"]},
                              "changes": _result_differences(base_replay.result, proposed), "impact": impact},
                preview_sequence=token["sequence"], submitted_sequence=sequence, request_version=version,
                submitter=actor, reviewer=None if impact_required else actor,
                reviewed_at=None if impact_required else timezone.now(),
            )
            operation = BusinessOperation.objects.create(
                operation_key=key, sequence=sequence, actor=actor, action=action, input_hash=input_hash,
                payload={"cost_correction_id": correction.pk, "source_kind": source["kind"], "source_id": source["id"],
                         "input_hash": input_hash}, cost_correction=correction,
            )
            if not impact_required:
                _record_source_input(source, correction)
                _persist_result(proposed, proposed_replay, base_replay.result, correction, operation)
            result = {"document_id": correction.pk, "cost_correction_id": correction.pk, "sequence": sequence,
                      "version": correction.version, "status": correction.status, "approval_required": impact_required}
            operation.result = result
            operation.save(update_fields=("result",))
            return result

    return _run_write(action, actor, request_id, write)


def approve_cost_correction(actor, correction_id, data, request_id=""):
    if not isinstance(data, dict) or set(data) - {"operation_key", "expected_version"}:
        raise OperationError("纠错审批数据格式无效。")
    correction_id = identifier(correction_id, "cost_correction_id")
    expected_version = quantity(data.get("expected_version"), "expected_version", positive=True)
    key = operation_uuid(data.get("operation_key"))
    input_hash = canonical_hash({"action": "cost_correction_approve", "correction_id": correction_id, "expected_version": expected_version})

    def write():
        with transaction.atomic():
            control = _lock_control()
            _require(actor, "approve_costcorrection")
            replayed = _operation_replay(actor, key, "cost_correction_approve", input_hash)
            if replayed is not None:
                return replayed
            correction = CostCorrection.objects.select_for_update().filter(pk=correction_id).first()
            if correction is None:
                raise OperationError("纠错记录不存在。", status=404, code="not_found")
            if correction.status != CostCorrection.Status.PENDING or correction.version != expected_version:
                raise OperationError("纠错状态或版本已变化。", status=409, code="version_conflict")
            if correction.submitter_id == actor.pk and not _owner(actor):
                raise OperationError("不能审批自己提交的纠错。", status=403, code="self_approval_denied")
            if control.last_sequence != correction.submitted_sequence:
                raise OperationError("提交纠错后业务数据已变化，请重新预览并提交。", status=409, code="stale_preview")
            source_kind = "receipt" if correction.receipt_id else "opening"
            source_id = correction.receipt_id or correction.opening_batch_id
            source = {"kind": source_kind, "id": source_id, "input": correction.proposed_input,
                      "reason": correction.reason, "supplemental_count_costs": correction.supplemental_count_costs}
            document, source_operation, current, version = _source(source_kind, source_id, lock=True)
            if version != correction.request_version or control.last_sequence != correction.submitted_sequence:
                raise OperationError("纠错预览已过期，请重新提交。", status=409, code="stale_preview")
            if canonical_hash(current) != canonical_hash(correction.original_input):
                raise OperationError("来源单据已变化，请重新预览。", status=409, code="version_conflict")
            _validate_references(actor, source, current)
            proposed, base_replay, proposed_replay, missing = _run_proposal(source, correction.supplemental_count_costs)
            if missing:
                raise OperationError("纠错回放需要历史盘盈成本。", status=409, code=COUNT_COST_MISSING,
                                     fields={"count_line_ids": missing})
            impact_required, impact, touched = _impact_after_source(
                source_kind, source_id, source_operation.sequence, current, correction.proposed_input,
                base_replay, base_replay.result, proposed,
            )
            if not impact_required:
                raise OperationError("纠错不再需要审批，请重新预览后由提交人直接更正。", status=409, code="stale_preview")
            sequence = _next_sequence(control)
            operation = BusinessOperation.objects.create(
                operation_key=key, sequence=sequence, actor=actor, action="cost_correction_approve",
                input_hash=input_hash, payload={"cost_correction_id": correction.pk, "expected_version": expected_version},
                cost_correction=correction,
            )
            _record_source_input(source, correction)
            correction.status = CostCorrection.Status.APPLIED
            correction.reviewer = actor
            correction.reviewed_at = timezone.now()
            correction.version += 1
            correction.before_after = {"source": {"before": current, "after": correction.proposed_input},
                                       "changes": _result_differences(base_replay.result, proposed), "impact": impact}
            correction.save(update_fields=("status", "reviewer", "reviewed_at", "version", "before_after"))
            _persist_result(proposed, proposed_replay, base_replay.result, correction, operation)
            result = {"document_id": correction.pk, "cost_correction_id": correction.pk, "sequence": sequence,
                      "version": correction.version, "status": correction.status}
            operation.result = result
            operation.save(update_fields=("result",))
            return result

    return _run_write("cost_correction_approve", actor, request_id, write)


def reject_cost_correction(actor, correction_id, data, request_id=""):
    if not isinstance(data, dict) or set(data) - {"operation_key", "expected_version"}:
        raise OperationError("纠错拒绝数据格式无效。")
    correction_id = identifier(correction_id, "cost_correction_id")
    expected_version = quantity(data.get("expected_version"), "expected_version", positive=True)
    key = operation_uuid(data.get("operation_key"))
    input_hash = canonical_hash({"action": "cost_correction_reject", "correction_id": correction_id, "expected_version": expected_version})

    def write():
        with transaction.atomic():
            control = _lock_control()
            _require(actor, "reject_costcorrection")
            replayed = _operation_replay(actor, key, "cost_correction_reject", input_hash)
            if replayed is not None:
                return replayed
            correction = CostCorrection.objects.select_for_update().filter(pk=correction_id).first()
            if correction is None:
                raise OperationError("纠错记录不存在。", status=404, code="not_found")
            if correction.status != CostCorrection.Status.PENDING or correction.version != expected_version:
                raise OperationError("纠错状态或版本已变化。", status=409, code="version_conflict")
            if correction.submitter_id == actor.pk and not _owner(actor):
                raise OperationError("不能拒绝自己提交的纠错。", status=403, code="self_approval_denied")
            sequence = _next_sequence(control)
            operation = BusinessOperation.objects.create(
                operation_key=key, sequence=sequence, actor=actor, action="cost_correction_reject", input_hash=input_hash,
                payload={"cost_correction_id": correction.pk, "expected_version": expected_version}, cost_correction=correction,
            )
            correction.status = CostCorrection.Status.REJECTED
            correction.reviewer = actor
            correction.reviewed_at = timezone.now()
            correction.version += 1
            correction.save(update_fields=("status", "reviewer", "reviewed_at", "version"))
            result = {"document_id": correction.pk, "cost_correction_id": correction.pk, "sequence": sequence,
                      "version": correction.version, "status": correction.status}
            operation.result = result
            operation.save(update_fields=("result",))
            return result

    return _run_write("cost_correction_reject", actor, request_id, write)


def cost_correction_rows(actor, correction_id=None, status=None):
    rows = CostCorrection.objects.select_related("receipt", "opening_batch", "submitter", "reviewer").order_by("-id")
    if correction_id is not None:
        rows = rows.filter(pk=correction_id)
    if status:
        if status not in CostCorrection.Status.values:
            raise OperationError("纠错状态筛选无效。", fields={"status": "请选择有效状态。"})
        rows = rows.filter(status=status)
    if not actor.has_perm("crm.view_all_sales_orders") and not actor.is_superuser:
        rows = rows.filter(submitter=actor) | rows.filter(reviewer=actor)
    result = []
    for row in rows.distinct():
        item = {
            "id": row.pk, "source_kind": "receipt" if row.receipt_id else "opening",
            "source_id": row.receipt_id or row.opening_batch_id, "status": row.status,
            "reason": row.reason, "version": row.version, "submitter_id": row.submitter_id,
            "reviewer_id": row.reviewer_id, "submitted_at": row.submitted_at.isoformat(),
            "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
            "preview_sequence": row.preview_sequence, "submitted_sequence": row.submitted_sequence,
            "request_version": row.request_version, "original_input": row.original_input,
            "proposed_input": row.proposed_input, "supplemental_count_costs": row.supplemental_count_costs,
            "before_after": visible_cost_correction_data(actor, row.before_after),
            "can_approve": row.status == CostCorrection.Status.PENDING and actor.has_perm("crm.approve_costcorrection")
                          and (row.submitter_id != actor.pk or _owner(actor)),
            "can_reject": row.status == CostCorrection.Status.PENDING and actor.has_perm("crm.reject_costcorrection")
                         and (row.submitter_id != actor.pk or _owner(actor)),
        }
        result.append(item)
    return result
