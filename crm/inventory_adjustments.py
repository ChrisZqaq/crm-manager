from decimal import Decimal, localcontext

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    BusinessOperation,
    DamageResolution,
    DamagedRecord,
    GiftLine,
    GiftRecord,
    InventoryBalance,
    InventoryMovement,
    InventoryHold,
    SKU,
    SplitRecord,
    StockCount,
    StockCountLine,
    Warehouse,
)
from .operations import (
    OperationError,
    _check_balance_result,
    _lock_control,
    _next_sequence,
    _operation_replay,
    _require,
    _run_write,
    amount,
    canonical_hash,
    identifier,
    operation_uuid,
    quantity,
)
from .permissions import ROLE_OWNER


def _outflow_cost(cost_amount, on_hand, outgoing):
    if outgoing <= 0 or outgoing > on_hand or on_hand <= 0:
        raise OperationError("可用在库数量不足。", status=409, code="stock_conflict")
    if outgoing == on_hand:
        return cost_amount
    with localcontext() as context:
        context.prec = 50
        return amount(cost_amount * outgoing / on_hand, "cost_amount")


def _balance_map(keys, *, lock=False):
    condition = Q(pk__in=[])
    for warehouse_id, sku_id in sorted(keys):
        condition |= Q(warehouse_id=warehouse_id, sku_id=sku_id)
    queryset = InventoryBalance.objects.filter(condition).order_by("warehouse_id", "sku_id", "id")
    if lock:
        queryset = queryset.select_for_update()
    return {(row.warehouse_id, row.sku_id): row for row in queryset}


def create_inventory_split(actor, data, operation_key, request_id=""):
    if not isinstance(data, dict) or set(data) - {
        "source_warehouse_id", "source_sku_id", "source_quantity",
        "target_warehouse_id", "target_sku_id", "target_quantity", "reason",
    }:
        raise OperationError("拆箱数据格式无效。")
    normalized = {
        "source_warehouse_id": identifier(data.get("source_warehouse_id"), "source_warehouse_id"),
        "source_sku_id": identifier(data.get("source_sku_id"), "source_sku_id"),
        "source_quantity": quantity(data.get("source_quantity"), "source_quantity", positive=True),
        "target_warehouse_id": identifier(data.get("target_warehouse_id"), "target_warehouse_id"),
        "target_sku_id": identifier(data.get("target_sku_id"), "target_sku_id"),
        "target_quantity": quantity(data.get("target_quantity"), "target_quantity", positive=True),
        "reason": str(data.get("reason") or "").strip(),
    }
    if not normalized["reason"]:
        raise OperationError("请填写拆箱原因。", fields={"reason": "此项必填。"})
    if normalized["source_sku_id"] == normalized["target_sku_id"]:
        raise OperationError("拆箱目标必须是不同SKU。", fields={"target_sku_id": "同SKU跨仓转移不属于拆箱。"})
    key = operation_uuid(operation_key)
    input_hash = canonical_hash({"action": "inventory_split", "input": normalized})

    def write():
        with transaction.atomic():
            control = _lock_control()
            _require(actor, "create_inventory_split")
            replay = _operation_replay(actor, key, "inventory_split", input_hash)
            if replay is not None:
                return replay
            warehouses = {
                row.pk: row for row in Warehouse.objects.select_for_update()
                .filter(pk__in={normalized["source_warehouse_id"], normalized["target_warehouse_id"]})
                .order_by("id")
            }
            skus = {
                row.pk: row for row in SKU.objects.select_for_update()
                .filter(pk__in={normalized["source_sku_id"], normalized["target_sku_id"]})
                .order_by("id")
            }
            if any(warehouse_id not in warehouses for warehouse_id in {
                normalized["source_warehouse_id"], normalized["target_warehouse_id"],
            }) or len(skus) != 2 or any(not item.is_active for item in warehouses.values()):
                raise OperationError("请选择启用中的仓库和两个不同SKU。", status=409, code="inactive_reference")
            if any(not item.is_active or not item.product.is_active or not item.product.brand.is_active for item in skus.values()):
                raise OperationError("请选择启用中的SKU、商品和IP。", status=409, code="inactive_reference")
            source_key = (normalized["source_warehouse_id"], normalized["source_sku_id"])
            target_key = (normalized["target_warehouse_id"], normalized["target_sku_id"])
            balances = _balance_map({source_key, target_key}, lock=True)
            source = balances.get(source_key)
            if source is None:
                raise OperationError("来源仓库SKU没有库存记录。", status=409, code="stock_conflict")
            target = balances.get(target_key)
            if target is None:
                target = InventoryBalance.objects.create(warehouse=warehouses[target_key[0]], sku=skus[target_key[1]])
                balances[target_key] = target
            available = source.on_hand - source.occupied - source.reserved - source.hoarded - source.damaged
            if normalized["source_quantity"] > available:
                raise OperationError("可拆数量超过来源可售库存。", status=409, code="stock_conflict")
            cost = _outflow_cost(source.cost_amount, source.on_hand, normalized["source_quantity"])
            _check_balance_result(source, on_hand=source.on_hand - normalized["source_quantity"], cost=source.cost_amount - cost)
            _check_balance_result(target, on_hand=target.on_hand + normalized["target_quantity"], cost=target.cost_amount + cost)
            sequence = _next_sequence(control)
            record = SplitRecord.objects.create(
                source_warehouse=warehouses[source_key[0]], source_sku=skus[source_key[1]],
                source_quantity=normalized["source_quantity"], target_warehouse=warehouses[target_key[0]],
                target_sku=skus[target_key[1]], target_quantity=normalized["target_quantity"],
                transferred_cost=cost, reason=normalized["reason"], created_by=actor,
            )
            operation = BusinessOperation.objects.create(
                operation_key=key, sequence=sequence, actor=actor, action="inventory_split",
                input_hash=input_hash, payload=normalized, split_record=record,
            )
            source.on_hand -= normalized["source_quantity"]
            source.cost_amount -= cost
            if source.on_hand == 0:
                source.cost_amount = Decimal("0.000")
            source.version += 1
            source.quantity_version += 1
            source.save(update_fields=("on_hand", "cost_amount", "version", "quantity_version"))
            target.on_hand += normalized["target_quantity"]
            target.cost_amount += cost
            target.version += 1
            target.quantity_version += 1
            target.save(update_fields=("on_hand", "cost_amount", "version", "quantity_version"))
            for balance, event, delta in (
                (source, "split_out", (-normalized["source_quantity"], -cost)),
                (target, "split_in", (normalized["target_quantity"], cost)),
            ):
                InventoryMovement.objects.create(
                    operation=operation, balance=balance, split_record=record, event_kind=event,
                    entry_key=f"warehouse:{balance.warehouse_id}:sku:{balance.sku_id}",
                    on_hand_delta=delta[0], cost_delta=delta[1], cost_revision=balance.version,
                )
            result = {"document_id": record.pk, "split_id": record.pk, "sequence": sequence, "transferred_cost": f"{cost:.3f}"}
            operation.result = result
            operation.save(update_fields=("result",))
            return result

    return _run_write("inventory_split", actor, request_id, write)


def create_inventory_gift(actor, data, operation_key, request_id=""):
    if not isinstance(data, dict) or set(data) - {"reason", "lines"}:
        raise OperationError("赠送数据格式无效。")
    reason = str(data.get("reason") or "").strip()
    rows = data.get("lines")
    if not reason or not isinstance(rows, list) or not rows:
        raise OperationError("赠送原因和商品明细均为必填。", fields={"reason": "此项必填。", "lines": "至少添加一条明细。"})
    lines = []
    seen = set()
    for index, row in enumerate(rows):
        prefix = f"lines[{index}]"
        if not isinstance(row, dict) or set(row) != {"warehouse_id", "sku_id", "quantity"}:
            raise OperationError("赠送明细格式无效。", fields={prefix: "每行需要仓库、SKU和数量。"})
        item = {
            "warehouse_id": identifier(row.get("warehouse_id"), f"{prefix}.warehouse_id"),
            "sku_id": identifier(row.get("sku_id"), f"{prefix}.sku_id"),
            "quantity": quantity(row.get("quantity"), f"{prefix}.quantity", positive=True),
        }
        stock_key = (item["warehouse_id"], item["sku_id"])
        if stock_key in seen:
            raise OperationError("同一仓库SKU不能重复列示。", fields={prefix: "请合并数量。"})
        seen.add(stock_key)
        lines.append(item)
    normalized = {"reason": reason, "lines": lines}
    key = operation_uuid(operation_key)
    input_hash = canonical_hash({"action": "inventory_gift", "input": normalized})

    def write():
        with transaction.atomic():
            control = _lock_control()
            _require(actor, "create_inventory_gift")
            replay = _operation_replay(actor, key, "inventory_gift", input_hash)
            if replay is not None:
                return replay
            warehouse_ids = {item["warehouse_id"] for item in lines}
            sku_ids = {item["sku_id"] for item in lines}
            warehouses = {row.pk: row for row in Warehouse.objects.select_for_update().filter(pk__in=warehouse_ids).order_by("id")}
            skus = {row.pk: row for row in SKU.objects.select_for_update().filter(pk__in=sku_ids).select_related("product__brand").order_by("id")}
            if len(warehouses) != len(warehouse_ids) or any(not row.is_active for row in warehouses.values()):
                raise OperationError("请选择启用中的仓库。", status=409, code="inactive_reference")
            if len(skus) != len(sku_ids) or any(not row.is_active or not row.product.is_active or not row.product.brand.is_active for row in skus.values()):
                raise OperationError("请选择启用中的SKU、商品和IP。", status=409, code="inactive_reference")
            keys = {(row["warehouse_id"], row["sku_id"]) for row in lines}
            balances = _balance_map(keys, lock=True)
            if balances.keys() != keys:
                raise OperationError("有商品没有对应库存记录。", status=409, code="stock_conflict")
            planned = []
            for item in lines:
                stock_key = (item["warehouse_id"], item["sku_id"])
                balance = balances[stock_key]
                available = balance.on_hand - balance.occupied - balance.reserved - balance.hoarded - balance.damaged
                if item["quantity"] > available:
                    raise OperationError("赠送数量超过可售库存。", status=409, code="stock_conflict", fields={f"lines[{len(planned)}].quantity": "可售库存不足。"})
                cost = _outflow_cost(balance.cost_amount, balance.on_hand, item["quantity"])
                _check_balance_result(balance, on_hand=balance.on_hand - item["quantity"], cost=balance.cost_amount - cost)
                planned.append({**item, "balance": balance, "cost": cost})
            sequence = _next_sequence(control)
            gift = GiftRecord.objects.create(reason=reason, created_by=actor)
            operation = BusinessOperation.objects.create(
                operation_key=key, sequence=sequence, actor=actor, action="inventory_gift",
                input_hash=input_hash, payload=normalized, gift=gift,
            )
            total_cost = Decimal("0.000")
            for index, item in enumerate(planned, 1):
                balance = item["balance"]
                line = GiftLine.objects.create(
                    gift=gift, line_number=index, warehouse=warehouses[item["warehouse_id"]],
                    sku=skus[item["sku_id"]], quantity=item["quantity"], cost_amount=item["cost"],
                )
                balance.on_hand -= item["quantity"]
                balance.cost_amount -= item["cost"]
                if balance.on_hand == 0:
                    balance.cost_amount = Decimal("0.000")
                balance.version += 1
                balance.quantity_version += 1
                balance.save(update_fields=("on_hand", "cost_amount", "version", "quantity_version"))
                InventoryMovement.objects.create(
                    operation=operation, balance=balance, gift_line=line, event_kind="gift_out",
                    entry_key=f"line:{line.pk}", on_hand_delta=-item["quantity"],
                    cost_delta=-item["cost"], cost_revision=balance.version,
                )
                total_cost += item["cost"]
            result = {"document_id": gift.pk, "gift_id": gift.pk, "sequence": sequence, "line_count": len(planned), "cost_amount": f"{total_cost:.3f}"}
            operation.result = result
            operation.save(update_fields=("result",))
            return result

    return _run_write("inventory_gift", actor, request_id, write)


def resolve_damaged_record(actor, damaged_record_id, data, request_id=""):
    if not isinstance(data, dict) or set(data) - {"operation_key", "expected_version", "kind", "quantity", "actual_refund", "unreturned_freight"}:
        raise OperationError("破损处理数据格式无效。")
    record_id = identifier(damaged_record_id, "damaged_record_id")
    kind = data.get("kind")
    if kind not in DamageResolution.Kind.values:
        raise OperationError("处理方式无效。", fields={"kind": "请选择换货或退款。"})
    normalized = {
        "damaged_record_id": record_id,
        "expected_version": quantity(data.get("expected_version"), "expected_version", positive=True),
        "kind": kind,
        "quantity": quantity(data.get("quantity"), "quantity", positive=True),
    }
    if kind == DamageResolution.Kind.REFUND:
        normalized["actual_refund"] = f"{amount(data.get('actual_refund'), 'actual_refund'):.3f}"
        normalized["unreturned_freight"] = f"{amount(data.get('unreturned_freight', 0), 'unreturned_freight'):.3f}"
    elif data.get("actual_refund") not in (None, "") or data.get("unreturned_freight") not in (None, "", 0, "0", "0.000"):
        raise OperationError("换货不能填写退款金额或未退运费。", fields={"kind": "换货不发生退款。"})
    key = operation_uuid(data.get("operation_key"))
    input_hash = canonical_hash({"action": "damage_resolution", "input": normalized})

    def write():
        with transaction.atomic():
            control = _lock_control()
            _require(actor, "resolve_damaged_record")
            replay = _operation_replay(actor, key, "damage_resolution", input_hash)
            if replay is not None:
                return replay
            record = DamagedRecord.objects.select_for_update().filter(pk=record_id).first()
            if record is None:
                raise OperationError("破损记录不存在。", status=404, code="not_found")
            if record.version != normalized["expected_version"]:
                raise OperationError("破损记录已变化，请刷新后重试。", status=409, code="version_conflict")
            if normalized["quantity"] > record.remaining_quantity:
                raise OperationError("处理数量超过待处理破损数量。", status=409, code="stock_conflict")
            balance = InventoryBalance.objects.select_for_update().get(pk=record.balance_id)
            if kind == DamageResolution.Kind.REFUND:
                cost = _outflow_cost(balance.cost_amount, balance.on_hand, normalized["quantity"])
                refund = Decimal(normalized["actual_refund"])
                difference = amount(cost - refund, "difference", nonnegative=False)
                _check_balance_result(
                    balance,
                    on_hand=balance.on_hand - normalized["quantity"],
                    damaged=balance.damaged - normalized["quantity"],
                    cost=balance.cost_amount - cost,
                )
            else:
                cost = Decimal("0.000")
                refund = None
                difference = Decimal("0.000")
                _check_balance_result(balance, damaged=balance.damaged - normalized["quantity"])
            sequence = _next_sequence(control)
            resolution = DamageResolution.objects.create(
                damaged_record=record, kind=kind, quantity=normalized["quantity"], refund_amount=refund,
                deducted_cost=cost, difference=difference,
                unreturned_freight=Decimal(normalized.get("unreturned_freight", "0.000")), created_by=actor,
            )
            operation = BusinessOperation.objects.create(
                operation_key=key, sequence=sequence, actor=actor, action="damage_resolution",
                input_hash=input_hash, payload=normalized, damage_resolution=resolution,
            )
            record.remaining_quantity -= normalized["quantity"]
            record.version += 1
            record.save(update_fields=("remaining_quantity", "version"))
            balance.damaged -= normalized["quantity"]
            if kind == DamageResolution.Kind.REFUND:
                balance.on_hand -= normalized["quantity"]
                balance.cost_amount -= cost
                if balance.on_hand == 0:
                    balance.cost_amount = Decimal("0.000")
                balance.quantity_version += 1
            balance.version += 1
            balance.save(update_fields=("damaged", "on_hand", "cost_amount", "version", "quantity_version"))
            InventoryMovement.objects.create(
                operation=operation, balance=balance, damage_resolution=resolution,
                event_kind="damage_refund" if kind == DamageResolution.Kind.REFUND else "damage_exchange",
                entry_key=f"resolution:{resolution.pk}",
                on_hand_delta=-normalized["quantity"] if kind == DamageResolution.Kind.REFUND else 0,
                damaged_delta=-normalized["quantity"], cost_delta=-cost, cost_revision=balance.version,
            )
            result = {
                "document_id": resolution.pk, "resolution_id": resolution.pk, "sequence": sequence,
                "remaining_quantity": record.remaining_quantity, "version": record.version,
                "deducted_cost": f"{cost:.3f}", "actual_refund": f"{refund:.3f}" if refund is not None else None,
                "difference": f"{difference:.3f}",
            }
            operation.result = result
            operation.save(update_fields=("result",))
            return result

    return _run_write("damage_resolution", actor, request_id, write)


def _count_input(data):
    reason = str(data.get("reason") or "").strip()
    rows = data.get("lines")
    if not reason or not isinstance(rows, list) or not rows:
        raise OperationError("盘点原因和明细均为必填。", fields={"reason": "此项必填。", "lines": "至少添加一条明细。"})
    lines = []
    seen = set()
    for index, row in enumerate(rows):
        prefix = f"lines[{index}]"
        if not isinstance(row, dict) or set(row) - {"warehouse_id", "sku_id", "observed_quantity", "cost_mode", "manual_unit_cost"}:
            raise OperationError("盘点明细格式无效。", fields={prefix: "明细包含未知字段。"})
        mode = row.get("cost_mode", StockCountLine.CostMode.AVERAGE)
        if mode not in StockCountLine.CostMode.values:
            raise OperationError("盘点计价方式无效。", fields={f"{prefix}.cost_mode": "请选择移动平均或人工输入。"})
        item = {
            "warehouse_id": identifier(row.get("warehouse_id"), f"{prefix}.warehouse_id"),
            "sku_id": identifier(row.get("sku_id"), f"{prefix}.sku_id"),
            "observed_quantity": quantity(row.get("observed_quantity"), f"{prefix}.observed_quantity"),
            "cost_mode": mode,
        }
        if mode == StockCountLine.CostMode.MANUAL:
            item["manual_unit_cost"] = f"{amount(row.get('manual_unit_cost'), f'{prefix}.manual_unit_cost'):.3f}"
        elif row.get("manual_unit_cost") not in (None, ""):
            raise OperationError("移动平均方式不能填写人工单位成本。", fields={f"{prefix}.manual_unit_cost": "请选择人工输入方式后填写。"})
        stock_key = (item["warehouse_id"], item["sku_id"])
        if stock_key in seen:
            raise OperationError("同一盘点不能重复登记仓库SKU。", fields={prefix: "请合并该SKU实盘数量。"})
        seen.add(stock_key)
        lines.append(item)
    return {"reason": reason, "lines": lines}


def create_stock_count(actor, data, operation_key, request_id=""):
    if not isinstance(data, dict) or set(data) - {"reason", "lines"}:
        raise OperationError("盘点数据格式无效。")
    normalized = _count_input(data)
    key = operation_uuid(operation_key)
    input_hash = canonical_hash({"action": "stock_count_submit", "input": normalized})

    def write():
        with transaction.atomic():
            control = _lock_control()
            _require(actor, "add_stockcount")
            replay = _operation_replay(actor, key, "stock_count_submit", input_hash)
            if replay is not None:
                return replay
            keys = {(item["warehouse_id"], item["sku_id"]) for item in normalized["lines"]}
            balances = _balance_map(keys, lock=True)
            if balances.keys() != keys:
                raise OperationError("盘点SKU没有库存记录，请先核对商品及库存。", status=409, code="stock_conflict")
            _validate_count_cost_modes(normalized["lines"], balances)
            count = StockCount.objects.create(reason=normalized["reason"], submitted_by=actor)
            saved = []
            for item in normalized["lines"]:
                balance = balances[(item["warehouse_id"], item["sku_id"])]
                line = StockCountLine.objects.create(
                    stock_count=count, balance=balance, warehouse_id=item["warehouse_id"], sku_id=item["sku_id"],
                    observed_quantity=item["observed_quantity"], captured_quantity_version=balance.quantity_version,
                    basis_cost_version=balance.version, cost_mode=item["cost_mode"],
                    manual_unit_cost=Decimal(item["manual_unit_cost"]) if "manual_unit_cost" in item else None,
                )
                saved.append({**item, "count_line_id": line.pk, "captured_quantity_version": balance.quantity_version, "basis_cost_version": balance.version})
            sequence = _next_sequence(control)
            operation = BusinessOperation.objects.create(
                operation_key=key, sequence=sequence, actor=actor, action="stock_count_submit", input_hash=input_hash,
                payload={"stock_count_id": count.pk, "reason": normalized["reason"], "lines": saved}, stock_count=count,
            )
            result = {"document_id": count.pk, "stock_count_id": count.pk, "sequence": sequence, "version": count.version, "status": count.status}
            operation.result = result
            operation.save(update_fields=("result",))
            return result

    return _run_write("stock_count_submit", actor, request_id, write)


def revise_stock_count(actor, count_id, data, request_id=""):
    if not isinstance(data, dict) or set(data) - {"operation_key", "expected_version", "reason", "lines"}:
        raise OperationError("盘点更新数据格式无效。")
    normalized = _count_input(data)
    count_id = identifier(count_id, "stock_count_id")
    expected_version = quantity(data.get("expected_version"), "expected_version", positive=True)
    key = operation_uuid(data.get("operation_key"))
    input_hash = canonical_hash({"action": "stock_count_resubmit", "stock_count_id": count_id, "expected_version": expected_version, "input": normalized})

    def write():
        with transaction.atomic():
            control = _lock_control()
            _require(actor, "add_stockcount")
            replay = _operation_replay(actor, key, "stock_count_resubmit", input_hash)
            if replay is not None:
                return replay
            count = StockCount.objects.select_for_update().filter(pk=count_id).first()
            if count is None:
                raise OperationError("盘点记录不存在。", status=404, code="not_found")
            if count.status != StockCount.Status.PENDING or count.version != expected_version:
                raise OperationError("盘点状态或版本已变化。", status=409, code="version_conflict")
            current_lines = { (line.warehouse_id, line.sku_id): line for line in count.lines.select_for_update().all() }
            keys = {(item["warehouse_id"], item["sku_id"]) for item in normalized["lines"]}
            balances = _balance_map(keys, lock=True)
            if balances.keys() != keys:
                raise OperationError("盘点SKU没有库存记录，请先核对商品及库存。", status=409, code="stock_conflict")
            _validate_count_cost_modes(normalized["lines"], balances)
            new_lines = []
            for item in normalized["lines"]:
                stock_key = (item["warehouse_id"], item["sku_id"])
                balance = balances[stock_key]
                line = current_lines.pop(stock_key, None)
                values = {
                    "balance": balance, "warehouse_id": item["warehouse_id"], "sku_id": item["sku_id"],
                    "observed_quantity": item["observed_quantity"], "captured_quantity_version": balance.quantity_version,
                    "basis_cost_version": balance.version, "cost_mode": item["cost_mode"],
                    "manual_unit_cost": Decimal(item["manual_unit_cost"]) if "manual_unit_cost" in item else None,
                }
                if line is None:
                    line = StockCountLine.objects.create(stock_count=count, **values)
                else:
                    for field, value in values.items():
                        setattr(line, field, value)
                    line.delta = 0
                    line.cost_delta = Decimal("0.000")
                    line.effective_unit_cost = Decimal("0.000")
                    line.supplemental_unit_cost = None
                    line.supplement_approval = None
                    line.save()
                new_lines.append({**item, "count_line_id": line.pk, "captured_quantity_version": balance.quantity_version, "basis_cost_version": balance.version})
            for line in current_lines.values():
                line.delete()
            count.reason = normalized["reason"]
            count.version += 1
            count.save(update_fields=("reason", "version"))
            sequence = _next_sequence(control)
            operation = BusinessOperation.objects.create(
                operation_key=key, sequence=sequence, actor=actor, action="stock_count_resubmit", input_hash=input_hash,
                payload={"stock_count_id": count.pk, "expected_version": expected_version, "reason": normalized["reason"], "lines": new_lines}, stock_count=count,
            )
            result = {"document_id": count.pk, "stock_count_id": count.pk, "sequence": sequence, "version": count.version, "status": count.status}
            operation.result = result
            operation.save(update_fields=("result",))
            return result

    return _run_write("stock_count_resubmit", actor, request_id, write)


def _count_line_preview(line, balance):
    delta = line.observed_quantity - balance.on_hand
    if delta == 0:
        cost = Decimal("0.000")
        unit_cost = Decimal("0.000")
    elif line.cost_mode == StockCountLine.CostMode.MANUAL:
        unit_cost = line.manual_unit_cost
        cost = amount(unit_cost * abs(delta), "cost_delta") * (1 if delta > 0 else -1)
    elif delta < 0:
        cost = -_outflow_cost(balance.cost_amount, balance.on_hand, -delta)
        unit_cost = amount(-cost / -delta, "effective_unit_cost")
    elif balance.on_hand > 0:
        with localcontext() as context:
            context.prec = 50
            average_cost = balance.cost_amount / balance.on_hand
            cost = amount(average_cost * delta, "cost_delta")
            unit_cost = amount(average_cost, "effective_unit_cost")
    elif line.supplemental_unit_cost is not None:
        unit_cost = line.supplemental_unit_cost
        cost = amount(unit_cost * delta, "cost_delta")
    else:
        raise OperationError(
            "盘盈没有可用成本，请补充历史单位成本。", status=409, code="count_cost_required",
            fields={"supplemental_count_costs": f"count_line_id={line.pk}"},
        )
    _check_balance_result(balance, on_hand=line.observed_quantity, cost=balance.cost_amount + cost)
    restricted = balance.occupied + balance.reserved + balance.hoarded + balance.damaged
    if restricted > line.observed_quantity:
        raise OperationError(
            "实盘数量不足覆盖当前占用和锁定。", status=409, code="stock_count_restricted",
            fields={"stock_count_line_id": str(line.pk), "observed_quantity": str(line.observed_quantity), "restricted_quantity": str(restricted)},
        )
    return delta, unit_cost, cost


def _validate_count_cost_modes(lines, balances):
    for index, item in enumerate(lines):
        if item["cost_mode"] != StockCountLine.CostMode.MANUAL:
            continue
        balance = balances[(item["warehouse_id"], item["sku_id"])]
        if balance.on_hand > 0 or item["observed_quantity"] <= 0:
            raise OperationError(
                "人工成本仅用于零库存盘盈；已有库存请按移动平均成本盘点。",
                fields={f"lines[{index}].cost_mode": "已有在库数量时必须使用移动平均；零库存人工成本仅适用于盘盈。"},
            )


def stock_count_rows(actor, count_id=None, status=None):
    if count_id is None:
        rows = StockCount.objects.all()
    else:
        rows = StockCount.objects.filter(pk=count_id)
    if status:
        if status not in StockCount.Status.values:
            raise OperationError("盘点状态筛选无效。", fields={"status": "请选择有效状态。"})
        rows = rows.filter(status=status)
    return [_stock_count_json(row) for row in rows.prefetch_related("lines__warehouse", "lines__sku", "lines__balance").order_by("-id")]


def _stock_count_json(count):
    lines = []
    for line in count.lines.select_related("warehouse", "sku", "balance").order_by("warehouse_id", "sku_id"):
        balance = line.balance
        preview = None
        try:
            delta, unit_cost, cost = _count_line_preview(line, balance)
            preview = {"delta": delta, "effective_unit_cost": f"{unit_cost:.3f}", "cost_delta": f"{cost:.3f}"}
        except OperationError as exc:
            preview = {"error": exc.code, "message": str(exc)}
        lines.append({
            "id": line.pk, "warehouse_id": line.warehouse_id, "warehouse": line.warehouse.name,
            "sku_id": line.sku_id, "sku": line.sku.code, "observed_quantity": line.observed_quantity,
            "captured_quantity_version": line.captured_quantity_version, "basis_cost_version": line.basis_cost_version,
            "current_quantity_version": balance.quantity_version, "current_balance_version": balance.version,
            "cost_mode": line.cost_mode, "manual_unit_cost": f"{line.manual_unit_cost:.3f}" if line.manual_unit_cost is not None else None,
            "supplemental_unit_cost": f"{line.supplemental_unit_cost:.3f}" if line.supplemental_unit_cost is not None else None,
            "approved_delta": line.delta, "approved_cost_delta": f"{line.cost_delta:.3f}",
            "approved_effective_unit_cost": f"{line.effective_unit_cost:.3f}", "preview": preview,
        })
    return {
        "id": count.pk, "status": count.status, "reason": count.reason, "version": count.version,
        "submitted_by": count.submitted_by_id, "approved_by": count.approved_by_id,
        "submitted_at": count.submitted_at.isoformat(), "approved_at": count.approved_at.isoformat() if count.approved_at else None,
        "lines": lines,
    }


def _is_owner(actor):
    return actor.groups.filter(name=ROLE_OWNER).exists()


def approve_stock_count(actor, count_id, data, request_id=""):
    if not isinstance(data, dict) or set(data) - {"operation_key", "expected_version", "balance_versions"}:
        raise OperationError("盘点审批数据格式无效。")
    count_id = identifier(count_id, "stock_count_id")
    expected_version = quantity(data.get("expected_version"), "expected_version", positive=True)
    versions = data.get("balance_versions", {})
    if not isinstance(versions, dict):
        raise OperationError("库存版本格式无效。", fields={"balance_versions": "请输入盘点行ID到库存版本的映射。"})
    normalized_versions = {str(identifier(k, "balance_versions")): quantity(v, "balance_versions", positive=True) for k, v in versions.items()}
    key = operation_uuid(data.get("operation_key"))
    input_hash = canonical_hash({"action": "stock_count_approve", "stock_count_id": count_id, "expected_version": expected_version, "balance_versions": normalized_versions})

    def write():
        with transaction.atomic():
            control = _lock_control()
            _require(actor, "approve_stockcount")
            replay = _operation_replay(actor, key, "stock_count_approve", input_hash)
            if replay is not None:
                return replay
            count = StockCount.objects.select_for_update().filter(pk=count_id).first()
            if count is None:
                raise OperationError("盘点记录不存在。", status=404, code="not_found")
            if count.status != StockCount.Status.PENDING or count.version != expected_version:
                raise OperationError("盘点状态或版本已变化。", status=409, code="version_conflict")
            if count.submitted_by_id == actor.pk and not _is_owner(actor):
                raise OperationError("不能审批自己提交的盘点。", status=403, code="self_approval_denied")
            lines = list(count.lines.select_for_update().select_related("balance").order_by("warehouse_id", "sku_id"))
            if not lines:
                raise OperationError("盘点没有明细。", status=409, code="state_conflict")
            balance_rows = list(InventoryBalance.objects.select_for_update()
                                .filter(pk__in=[line.balance_id for line in lines]).order_by("warehouse_id", "sku_id", "id"))
            balances = {row.pk: row for row in balance_rows}
            current_versions = {str(line.pk): balances[line.balance_id].version for line in lines}
            stale_quantity = [line.pk for line in lines if balances[line.balance_id].quantity_version != line.captured_quantity_version]
            if stale_quantity:
                raise OperationError("盘点后在库数量发生过变化，请重新核对并提交受影响明细。", status=409, code="quantity_anchor_stale", fields={"stock_count_line_ids": stale_quantity})
            if normalized_versions != current_versions:
                raise OperationError("盘点金额预览已变化，请刷新并确认最新影响。", status=409, code="stale_preview", fields={"balance_versions": current_versions})
            planned = []
            for line in lines:
                balance = balances[line.balance_id]
                delta, unit_cost, cost = _count_line_preview(line, balance)
                planned.append((line, balance, delta, unit_cost, cost))
            sequence = _next_sequence(control)
            now = timezone.now()
            operation = BusinessOperation.objects.create(
                operation_key=key, sequence=sequence, actor=actor, action="stock_count_approve", input_hash=input_hash,
                payload={"stock_count_id": count.pk, "expected_version": expected_version,
                         "lines": [{"count_line_id": line.pk, "warehouse_id": line.warehouse_id, "sku_id": line.sku_id,
                                   "observed_quantity": line.observed_quantity, "quantity_version": balance.quantity_version,
                                   "balance_version": balance.version, "delta": delta, "cost_mode": line.cost_mode,
                                   "effective_unit_cost": f"{unit_cost:.3f}", "cost_delta": f"{cost:.3f}"}
                                  for line, balance, delta, unit_cost, cost in planned]},
                stock_count=count,
            )
            for line, balance, delta, unit_cost, cost in planned:
                _check_balance_result(balance, on_hand=line.observed_quantity, cost=balance.cost_amount + cost)
                if delta or cost:
                    balance.on_hand = line.observed_quantity
                    balance.cost_amount += cost
                    if balance.on_hand == 0:
                        balance.cost_amount = Decimal("0.000")
                    balance.version += 1
                    if delta:
                        balance.quantity_version += 1
                    balance.save(update_fields=("on_hand", "cost_amount", "version", "quantity_version"))
                line.delta = delta
                line.effective_unit_cost = unit_cost
                line.cost_delta = cost
                line.save(update_fields=("delta", "effective_unit_cost", "cost_delta"))
                InventoryMovement.objects.create(
                    operation=operation, balance=balance, stock_count_line=line, event_kind="stock_count",
                    entry_key=f"line:{line.pk}", on_hand_delta=delta, cost_delta=cost,
                    cost_revision=balance.version,
                )
            count.status = StockCount.Status.APPROVED
            count.approved_by = actor
            count.approved_at = now
            count.version += 1
            count.save(update_fields=("status", "approved_by", "approved_at", "version"))
            result = {"document_id": count.pk, "stock_count_id": count.pk, "sequence": sequence, "version": count.version, "status": count.status}
            operation.result = result
            operation.save(update_fields=("result",))
            return result

    return _run_write("stock_count_approve", actor, request_id, write)
