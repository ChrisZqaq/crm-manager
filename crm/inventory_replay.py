from decimal import Decimal, localcontext

from .inventory_adjustments import _outflow_cost
from .models import (
    BusinessOperation,
    CostCorrection,
    DamageResolution,
    DamagedRecord,
    GiftLine,
    InventoryBalance,
    InventoryHold,
    InventoryMovement,
    OpeningBatch,
    OpeningLine,
    Receipt,
    ReceiptLine,
    SalesOrder,
    SalesOrderLine,
    SplitRecord,
    StockCount,
    StockCountLine,
)
from .operations import OperationError, amount


ZERO = Decimal("0.000")
COUNT_COST_MISSING = "count_cost_required"
INVENTORY_ACTIONS = {
    "opening", "receipt", "hold_create", "hold_release", "sales_order_submit", "sales_order_amend",
    "sales_order_cancel", "sales_order_ship", "sales_order_onsite_delivery", "inventory_split",
    "inventory_gift", "damage_resolution", "stock_count_approve",
}


class ReplayFailure(OperationError):
    pass


class ReplayResult:
    def __init__(self):
        self.balances = {}
        self.holds = {}
        self.damage = {}
        self.order_lines = {}
        self.orders = {}
        self.movements = {}
        self.split_costs = {}
        self.gift_costs = {}
        self.damage_costs = {}
        self.count_costs = {}
        self.fulfillment_costs = {}
        self.supplemented_count_lines = set()
        self.operations = []

    def balance(self, key):
        return self.balances.setdefault(key, {
            "on_hand": 0, "occupied": 0, "reserved": 0, "hoarded": 0, "damaged": 0, "cost_amount": ZERO,
        })


def _fail(message, *, code="correction_replay_invalid", fields=None):
    raise ReplayFailure(message, status=409, code=code, fields=fields or {})


def _as_decimal(value):
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _apply(balance, *, on_hand=0, occupied=0, reserved=0, hoarded=0, damaged=0, cost=ZERO):
    balance["on_hand"] += on_hand
    balance["occupied"] += occupied
    balance["reserved"] += reserved
    balance["hoarded"] += hoarded
    balance["damaged"] += damaged
    balance["cost_amount"] = amount(balance["cost_amount"] + _as_decimal(cost), "cost_amount")
    if min(balance["on_hand"], balance["occupied"], balance["reserved"], balance["hoarded"], balance["damaged"]) < 0:
        _fail("历史库存数量或限制数量将变为负数，纠错未生效。")
    if balance["on_hand"] < 0:
        _fail("历史实际在库数量将变为负数，纠错未生效。", fields={"on_hand": str(balance["on_hand"])})
    if balance["cost_amount"] < 0:
        _fail("历史库存成本将变为负数，纠错未生效。", fields={"cost_amount": str(balance["cost_amount"])})
    if balance["on_hand"] == 0 and balance["cost_amount"] != ZERO:
        _fail("库存数量归零但仍有成本余额，回放不一致。", fields={"cost_amount": str(balance["cost_amount"])})


def _emit(result, operation, event_kind, entry_key, key, source, *, on_hand=0, occupied=0,
          reserved=0, hoarded=0, damaged=0, cost=ZERO):
    identity = (operation.pk, event_kind, entry_key)
    if identity in result.movements:
        _fail("业务流水输入存在重复明细，不能安全回放。", fields={"operation_id": str(operation.pk), "entry_key": entry_key})
    result.movements[identity] = {
        "key": key, "source": source, "on_hand_delta": on_hand, "occupied_delta": occupied,
        "reserved_delta": reserved, "hoarded_delta": hoarded, "damaged_delta": damaged,
        "cost_delta": amount(_as_decimal(cost), "cost_delta", nonnegative=False),
    }


def effective_receipt_input(receipt):
    return {
        "warehouse_id": receipt.warehouse_id,
        "supplier_id": receipt.supplier_id,
        "lines": [{
            "line_number": line.line_number, "sku_id": line.sku_id, "quantity": line.quantity,
            "purchase_price": f"{line.purchase_price:.3f}", "inbound_freight": f"{line.inbound_freight:.3f}",
            "reserved_quantity": line.reserved_quantity, "damaged_quantity": line.damaged_quantity,
            "low_stock_threshold": line.low_stock_threshold,
        } for line in receipt.lines.order_by("line_number", "id")],
    }


def effective_opening_input(batch):
    return {"lines": [{
        "line_number": line.line_number, "warehouse_id": line.warehouse_id, "sku_id": line.sku_id,
        "quantity": line.quantity, "unit_cost": f"{line.unit_cost:.3f}",
        "reserved_quantity": line.reserved_quantity, "hoarded_quantity": line.hoarded_quantity,
        "damaged_quantity": line.damaged_quantity,
    } for line in batch.lines.order_by("line_number", "id")]}


class InventoryReplay:
    def __init__(self, source_override=None, supplemental_count_costs=None):
        self.source_override = source_override
        self.supplemental_count_costs = supplemental_count_costs or {}
        self.result = ReplayResult()
        self.operations = list(BusinessOperation.objects.exclude(sequence__isnull=True).order_by("sequence", "id"))
        if BusinessOperation.objects.filter(sequence__isnull=True).exists():
            _fail("存在没有全局顺序号的业务操作，无法可靠重建库存。", fields={"business_operation": "请先补齐顺序记录。"})
        self.movements = list(InventoryMovement.objects.select_related("balance").order_by("operation_id", "id"))
        self.moves_by_operation = {}
        for move in self.movements:
            self.moves_by_operation.setdefault(move.operation_id, []).append(move)

        receipt_lines = list(ReceiptLine.objects.select_related("receipt").order_by("receipt_id", "line_number", "id"))
        opening_lines = list(OpeningLine.objects.select_related("batch").order_by("batch_id", "line_number", "id"))
        self.receipt_line_by = {(line.receipt_id, line.line_number): line for line in receipt_lines}
        self.opening_line_by = {(line.batch_id, line.line_number): line for line in opening_lines}
        self.receipt_lines_by_receipt = {}
        for line in receipt_lines:
            self.receipt_lines_by_receipt.setdefault(line.receipt_id, []).append(line)
        self.opening_lines_by_batch = {}
        for line in opening_lines:
            self.opening_lines_by_batch.setdefault(line.batch_id, []).append(line)
        self.hold_rows = list(InventoryHold.objects.select_related("balance").order_by("id"))
        self.hold_by_id = {row.pk: row for row in self.hold_rows}
        self.hold_for_source = {}
        for row in self.hold_rows:
            source_id = row.receipt_line_id or row.opening_line_id
            source_kind = "receipt" if row.receipt_line_id else "opening"
            if source_id:
                ident = (source_kind, source_id, row.type)
                if ident in self.hold_for_source:
                    _fail("同一入库明细存在重复限制记录，无法安全回放。", fields={"hold_id": str(row.pk)})
                self.hold_for_source[ident] = row

        self.damage_rows = list(DamagedRecord.objects.select_related("balance").order_by("id"))
        self.damage_by_id = {row.pk: row for row in self.damage_rows}
        self.damage_for_source = {}
        for row in self.damage_rows:
            source_id = row.receipt_line_id or row.opening_line_id
            source_kind = "receipt" if row.receipt_line_id else "opening"
            ident = (source_kind, source_id)
            if ident in self.damage_for_source:
                _fail("同一入库明细存在重复破损记录，无法安全回放。", fields={"damaged_record_id": str(row.pk)})
            self.damage_for_source[ident] = row

        self.split_rows = {row.pk: row for row in SplitRecord.objects.all()}
        self.gift_lines = {row.pk: row for row in GiftLine.objects.select_related("gift").all()}
        self.damage_resolutions = {row.pk: row for row in DamageResolution.objects.select_related("damaged_record").all()}
        self.count_lines = {row.pk: row for row in StockCountLine.objects.select_related("stock_count", "balance").all()}
        self.source_receipts = {row.pk: row for row in Receipt.objects.all()}
        self.source_openings = {row.pk: row for row in OpeningBatch.objects.all()}
        self.db_balances = {(row.warehouse_id, row.sku_id): row for row in InventoryBalance.objects.all()}
        self.db_order_lines = {row.pk: row for row in SalesOrderLine.objects.all()}
        self.db_orders = {row.pk: row for row in SalesOrder.objects.all()}

    def _source_rows(self, kind, source_id):
        if self.source_override and self.source_override.get("kind") == kind and self.source_override.get("id") == source_id:
            return self.source_override
        if kind == "receipt":
            return effective_receipt_input(self.source_receipts[source_id])
        return effective_opening_input(self.source_openings[source_id])

    def _source_stock_event(self, operation, kind):
        source_id = operation.receipt_id if kind == "receipt" else operation.opening_batch_id
        doc = self._source_rows(kind, source_id)
        if kind == "receipt":
            line_by_number = self.receipt_line_by
            source_relation = "receipt_line"
        else:
            line_by_number = self.opening_line_by
            source_relation = "opening_line"
        for item in doc["lines"]:
            number = int(item["line_number"])
            line = line_by_number.get((source_id, number))
            if line is None:
                _fail("来源单据行与固定业务行ID不一致，不能回放。", fields={"line_number": str(number)})
            warehouse_id = int(doc["warehouse_id"]) if kind == "receipt" else int(item["warehouse_id"])
            sku_id = int(item["sku_id"])
            quantity_value = int(item["quantity"])
            reserved = int(item.get("reserved_quantity", 0))
            hoarded = int(item.get("hoarded_quantity", 0))
            damaged = int(item.get("damaged_quantity", 0))
            if kind == "receipt":
                price = Decimal(item["purchase_price"])
                freight = Decimal(item["inbound_freight"])
                cost = amount(price * quantity_value + freight, "cost_amount")
            else:
                unit_cost = Decimal(item["unit_cost"])
                cost = amount(unit_cost * quantity_value, "cost_amount")
            if line.pk is None:
                _fail("来源单据行缺少稳定ID，不能回放。")
            key = (warehouse_id, sku_id)
            balance = self.result.balance(key)
            _apply(balance, on_hand=quantity_value, reserved=reserved, hoarded=hoarded, damaged=damaged, cost=cost)
            for hold_type, hold_qty in ((InventoryHold.Type.CUSTOMER_RESERVATION, reserved), (InventoryHold.Type.HOARD, hoarded)):
                if not hold_qty and (kind, line.pk, hold_type) not in self.hold_for_source:
                    continue
                row = self.hold_for_source.get((kind, line.pk, hold_type))
                hold_id = row.pk if row else f"{kind}:{line.pk}:{hold_type}"
                self.result.holds[hold_id] = {
                    "initial_quantity": hold_qty, "remaining_quantity": hold_qty, "type": hold_type,
                    "key": key, "receipt_line_id": line.pk if kind == "receipt" else None,
                    "opening_line_id": line.pk if kind == "opening" else None,
                    "created_by_id": row.created_by_id if row else operation.actor_id,
                }
            damage_row = self.damage_for_source.get((kind, line.pk))
            if damaged or damage_row:
                damage_id = damage_row.pk if damage_row else f"{kind}:{line.pk}:damage"
                self.result.damage[damage_id] = {
                    "original_quantity": damaged, "remaining_quantity": damaged, "key": key,
                    "receipt_line_id": line.pk if kind == "receipt" else None,
                    "opening_line_id": line.pk if kind == "opening" else None,
                    "created_by_id": damage_row.created_by_id if damage_row else operation.actor_id,
                }
            self._emit(operation, kind, line, key, quantity_value, reserved, hoarded, damaged, cost)

    def _emit(self, operation, kind, line, key, qty, reserved, hoarded, damaged, cost):
        source_field = "receipt_line" if kind == "receipt" else "opening_line"
        self._record_movement(operation, kind, f"line:{line.pk}", key, (source_field, line.pk),
                              on_hand=qty, reserved=reserved, hoarded=hoarded, damaged=damaged, cost=cost)

    def _record_movement(self, operation, event_kind, entry_key, key, source, **deltas):
        _emit(self.result, operation, event_kind, entry_key, key, source, **deltas)

    def run(self):
        for operation in self.operations:
            action = operation.action
            if action in ("opening", "receipt"):
                self._source_stock_event(operation, action)
            elif action == "hold_create":
                self._hold_create(operation)
            elif action == "hold_release":
                self._hold_release(operation)
            elif action in ("sales_order_submit", "sales_order_amend"):
                self._order_input(operation)
            elif action == "sales_order_cancel":
                self._order_cancel(operation)
            elif action in ("sales_order_ship", "sales_order_onsite_delivery"):
                self._fulfillment(operation)
            elif action == "sales_order_freight":
                self._freight(operation)
            elif action == "inventory_split":
                self._split(operation)
            elif action == "inventory_gift":
                self._gift(operation)
            elif action == "damage_resolution":
                self._damage_resolution(operation)
            elif action == "stock_count_approve":
                self._stock_count(operation)
            else:
                if self.moves_by_operation.get(operation.pk):
                    _fail("发现尚未纳入回放的库存操作，不能批准纠错。", fields={"action": action, "sequence": str(operation.sequence)})
            self.result.operations.append(operation)
        self._finalize_orders()
        self._validate_current_constraints()
        return self.result

    def _hold_create(self, operation):
        data = operation.payload
        hold_id = operation.hold_id
        hold = self.hold_by_id.get(hold_id)
        if hold is None:
            _fail("限制操作缺少对应记录。", fields={"operation_id": str(operation.pk)})
        key = (int(data["warehouse_id"]), int(data["sku_id"]))
        kind = data["type"]
        qty = int(data["quantity"])
        state = self.result.balance(key)
        self.result.holds[hold_id] = {
            "initial_quantity": qty, "remaining_quantity": qty, "type": kind, "key": key,
            "receipt_line_id": None, "opening_line_id": None, "created_by_id": hold.created_by_id,
        }
        field = "reserved" if kind == InventoryHold.Type.CUSTOMER_RESERVATION else "hoarded"
        _apply(state, **{field: qty})
        self._record_movement(operation, "hold_create", f"hold:{hold_id}", key, ("hold", hold_id), **{field: qty})

    def _hold_release(self, operation):
        data = operation.payload
        hold_id = int(data["hold_id"])
        hold = self.result.holds.get(hold_id)
        qty = int(data["quantity"])
        if hold is None or qty > hold["remaining_quantity"]:
            _fail("历史解锁数量超过纠错后的限制数量，不能批准。", fields={"hold_id": str(hold_id), "quantity": str(qty)})
        hold["remaining_quantity"] -= qty
        key = hold["key"]
        field = "reserved" if hold["type"] == InventoryHold.Type.CUSTOMER_RESERVATION else "hoarded"
        _apply(self.result.balance(key), **{field: -qty})
        entry_key = f"release:{int(data['expected_version']) + 1}"
        self._record_movement(operation, "hold_release", entry_key, key, ("hold", hold_id), **{field: -qty})

    def _order_input(self, operation):
        order_id = operation.sales_order_id or int(operation.payload.get("order_id"))
        payload = operation.payload
        old_active = {line_id: line for line_id, line in self.result.order_lines.items() if line["order_id"] == order_id and line["active"]}
        old_totals = {}
        for line in old_active.values():
            key = (line["warehouse_id"], line["sku_id"])
            old_totals[key] = old_totals.get(key, 0) + line["quantity"]
        planned = {}
        recalculate = bool(payload.get("recalculate_all_costs"))
        for item in payload.get("lines", []):
            line_id = int(item["line_id"])
            key = (int(item["warehouse_id"]), int(item["sku_id"]))
            qty = int(item["quantity"])
            old = self.result.order_lines.get(line_id)
            same = bool(old and old["order_id"] == order_id and (old["warehouse_id"], old["sku_id"]) == key and old["active"])
            if recalculate or not same or qty > old["quantity"]:
                balance = self.result.balance(key)
                if balance["on_hand"] <= 0:
                    _fail("历史订单成本缺少当时的库存数量。", fields={"order_id": str(order_id), "line_id": str(line_id)})
                with localcontext() as context:
                    context.prec = 50
                    unit_cost = amount(balance["cost_amount"] / balance["on_hand"], "unit_cost_snapshot")
            elif same:
                unit_cost = old["unit_cost_snapshot"]
            else:
                _fail("订单成本操作缺少稳定的上游订单行。", fields={"order_id": str(order_id), "line_id": str(line_id)})
            price = Decimal(item["sale_price"])
            revenue = amount(price * qty, "revenue_amount")
            cost = amount(unit_cost * qty, "order_cost_amount")
            planned[line_id] = {
                "line_id": line_id, "order_id": order_id, "warehouse_id": key[0], "sku_id": key[1],
                "quantity": qty, "unit_price": price, "revenue_amount": revenue,
                "unit_cost_snapshot": unit_cost, "order_cost_amount": cost,
                "cost_mode": item["cost_mode"], "outbound_freight": old["outbound_freight"] if same else ZERO,
                "active": True,
            }
        new_totals = {}
        for line in planned.values():
            stock_key = (line["warehouse_id"], line["sku_id"])
            new_totals[stock_key] = new_totals.get(stock_key, 0) + line["quantity"]
        for line_id, line in old_active.items():
            if line_id not in planned:
                line["active"] = False
        for line_id, line in planned.items():
            self.result.order_lines[line_id] = line
        for stock_key in sorted(old_totals.keys() | new_totals.keys()):
            delta = new_totals.get(stock_key, 0) - old_totals.get(stock_key, 0)
            if delta:
                _apply(self.result.balance(stock_key), occupied=delta)
                event_kind = "order_amend" if operation.action == "sales_order_amend" else "order_submit"
                self._record_movement(
                    operation, event_kind, f"warehouse:{stock_key[0]}:sku:{stock_key[1]}",
                    stock_key, ("sales_order", order_id), occupied=delta,
                )
        self.result.orders.setdefault(order_id, {})

    def _order_cancel(self, operation):
        order_id = operation.sales_order_id or int(operation.payload["order_id"])
        for item in operation.payload.get("released_holds", []):
            key = (int(item["warehouse_id"]), int(item["sku_id"]))
            qty = int(item["quantity"])
            _apply(self.result.balance(key), occupied=-qty)
            self._record_movement(operation, "order_cancel", f"warehouse:{key[0]}:sku:{key[1]}", key,
                                  ("sales_order", order_id), occupied=-qty)

    def _apply_freight_rows(self, operation):
        for item in operation.payload.get("outbound_freights", []):
            line_id = int(item["line_id"])
            line = self.result.order_lines.get(line_id)
            if line is None or not line["active"]:
                _fail("运费操作引用了不存在的有效订单行。", fields={"line_id": str(line_id)})
            line["outbound_freight"] = Decimal(item["amount"])

    def _freight(self, operation):
        self._apply_freight_rows(operation)

    def _fulfillment(self, operation):
        self._apply_freight_rows(operation)
        total_cost = ZERO
        total_quantity = 0
        rows = [move for move in self.moves_by_operation.get(operation.pk, []) if move.event_kind == "order_outbound"]
        if not rows:
            _fail("实际交付操作缺少固定出库数量流水。", fields={"operation_id": str(operation.pk)})
        order_id = operation.sales_order_id or int(operation.payload["order_id"])
        for move in rows:
            key = (move.balance.warehouse_id, move.balance.sku_id)
            outgoing = -move.on_hand_delta
            if outgoing <= 0:
                _fail("实际出库流水数量无效。", fields={"movement_id": str(move.pk)})
            balance = self.result.balance(key)
            if balance["occupied"] < outgoing:
                _fail("纠错后实际交付量超过历史有效订单占货。", fields={"order_id": str(order_id), "warehouse_id": str(key[0]), "sku_id": str(key[1])})
            cost = _outflow_cost(balance["cost_amount"], balance["on_hand"], outgoing)
            _apply(balance, on_hand=-outgoing, occupied=-outgoing, cost=-cost)
            self._record_movement(operation, "order_outbound", move.entry_key, key, ("sales_order", order_id),
                                  on_hand=-outgoing, occupied=-outgoing, cost=-cost)
            total_cost += cost
            total_quantity += outgoing
        self.result.fulfillment_costs[operation.pk] = {"cost": amount(total_cost, "actual_inventory_cost"), "quantity": total_quantity}

    def _split(self, operation):
        record = self.split_rows.get(operation.split_record_id)
        if record is None:
            _fail("拆箱操作缺少拆箱记录。", fields={"operation_id": str(operation.pk)})
        source_key = (record.source_warehouse_id, record.source_sku_id)
        target_key = (record.target_warehouse_id, record.target_sku_id)
        source_qty = record.source_quantity
        target_qty = record.target_quantity
        source = self.result.balance(source_key)
        cost = _outflow_cost(source["cost_amount"], source["on_hand"], source_qty)
        _apply(source, on_hand=-source_qty, cost=-cost)
        _apply(self.result.balance(target_key), on_hand=target_qty, cost=cost)
        self.result.split_costs[record.pk] = cost
        self._record_movement(operation, "split_out", f"warehouse:{source_key[0]}:sku:{source_key[1]}", source_key,
                              ("split_record", record.pk), on_hand=-source_qty, cost=-cost)
        self._record_movement(operation, "split_in", f"warehouse:{target_key[0]}:sku:{target_key[1]}", target_key,
                              ("split_record", record.pk), on_hand=target_qty, cost=cost)

    def _gift(self, operation):
        lines = sorted((line for line in self.gift_lines.values() if line.gift_id == operation.gift_id), key=lambda line: line.line_number)
        if not lines:
            _fail("赠送操作没有固定明细。", fields={"operation_id": str(operation.pk)})
        for line in lines:
            key = (line.warehouse_id, line.sku_id)
            balance = self.result.balance(key)
            cost = _outflow_cost(balance["cost_amount"], balance["on_hand"], line.quantity)
            _apply(balance, on_hand=-line.quantity, cost=-cost)
            self.result.gift_costs[line.pk] = cost
            self._record_movement(operation, "gift_out", f"line:{line.pk}", key, ("gift_line", line.pk),
                                  on_hand=-line.quantity, cost=-cost)

    def _damage_resolution(self, operation):
        resolution = self.damage_resolutions.get(operation.damage_resolution_id)
        if resolution is None:
            _fail("破损处理操作缺少处理记录。", fields={"operation_id": str(operation.pk)})
        record = resolution.damaged_record
        damage = self.result.damage.get(record.pk)
        if damage is None or resolution.quantity > damage["remaining_quantity"]:
            _fail("历史破损处理超过纠错后的待处理数量。", fields={"damaged_record_id": str(record.pk)})
        key = damage["key"]
        damage["remaining_quantity"] -= resolution.quantity
        balance = self.result.balance(key)
        kind = resolution.kind
        if kind == DamageResolution.Kind.REFUND:
            cost = _outflow_cost(balance["cost_amount"], balance["on_hand"], resolution.quantity)
            refund = resolution.refund_amount
            difference = amount(cost - refund, "difference", nonnegative=False)
            _apply(balance, on_hand=-resolution.quantity, damaged=-resolution.quantity, cost=-cost)
            event = "damage_refund"
        elif kind == DamageResolution.Kind.EXCHANGE:
            cost = ZERO
            difference = ZERO
            _apply(balance, damaged=-resolution.quantity)
            event = "damage_exchange"
        else:
            _fail("破损处理方式超出可回放版本。", fields={"kind": kind})
        self.result.damage_costs[resolution.pk] = {"cost": cost, "difference": difference}
        self._record_movement(operation, event, f"resolution:{resolution.pk}", key,
                              ("damage_resolution", resolution.pk), on_hand=-resolution.quantity if kind == "refund" else 0,
                              damaged=-resolution.quantity, cost=-cost)

    def _count_cost(self, line, balance):
        delta = line.observed_quantity - balance["on_hand"]
        if delta == 0:
            return 0, ZERO, ZERO
        if line.cost_mode == StockCountLine.CostMode.MANUAL:
            unit = line.manual_unit_cost
            cost = amount(unit * abs(delta), "cost_delta") * (1 if delta > 0 else -1)
        elif delta < 0:
            cost = -_outflow_cost(balance["cost_amount"], balance["on_hand"], -delta)
            unit = amount(-cost / -delta, "effective_unit_cost")
        elif balance["on_hand"] > 0:
            with localcontext() as context:
                context.prec = 50
                average_cost = balance["cost_amount"] / balance["on_hand"]
                cost = amount(average_cost * delta, "cost_delta")
                unit = amount(average_cost, "effective_unit_cost")
        else:
            unit = self.supplemental_count_costs.get(line.pk, line.supplemental_unit_cost)
            if unit is None:
                _fail("历史盘盈成本来源已不存在，请补充盘点单位成本后重新预览。", code=COUNT_COST_MISSING,
                      fields={"count_line_id": str(line.pk)})
            unit = Decimal(unit)
            cost = amount(unit * delta, "cost_delta")
            if line.pk in self.supplemental_count_costs:
                self.result.supplemented_count_lines.add(line.pk)
        return delta, unit, cost

    def _stock_count(self, operation):
        lines = sorted((line for line in self.count_lines.values() if line.stock_count_id == operation.stock_count_id),
                       key=lambda line: (line.warehouse_id, line.sku_id, line.pk))
        for line in lines:
            key = (line.warehouse_id, line.sku_id)
            balance = self.result.balance(key)
            delta, unit, cost = self._count_cost(line, balance)
            if balance["occupied"] + balance["reserved"] + balance["hoarded"] + balance["damaged"] > line.observed_quantity:
                # Historical restrictions may be released or cancelled later; only the final state is a hard gate.
                pass
            _apply(balance, on_hand=delta, cost=cost)
            self.result.count_costs[line.pk] = {"delta": delta, "unit_cost": unit, "cost": cost}
            self._record_movement(operation, "stock_count", f"line:{line.pk}", key,
                                  ("stock_count_line", line.pk), on_hand=delta, cost=cost)

    def _finalize_orders(self):
        for order_id, state in self.result.orders.items():
            lines = [line for line in self.result.order_lines.values() if line["order_id"] == order_id and line["active"]]
            revenue = amount(sum((line["revenue_amount"] for line in lines), ZERO), "revenue_amount")
            cost = amount(sum((line["order_cost_amount"] for line in lines), ZERO), "cost_amount")
            freight = amount(sum((line["outbound_freight"] for line in lines), ZERO), "outbound_freight")
            margin = amount(revenue - cost - freight, "margin_amount", nonnegative=False)
            commission = amount(margin * Decimal("0.10"), "commission_estimate", nonnegative=False)
            state.update({"revenue_amount": revenue, "cost_amount": cost, "margin_amount": margin,
                          "commission_estimate": commission, "freight_amount": freight})

    def _validate_current_constraints(self):
        keys = set(self.db_balances) | set(self.result.balances)
        for key in keys:
            state = self.result.balance(key)
            restricted = state["occupied"] + state["reserved"] + state["hoarded"] + state["damaged"]
            if restricted > state["on_hand"]:
                _fail("回放后的当前受限库存超过在库量；请先处理订单占用或限制记录。", fields={
                    "warehouse_id": str(key[0]), "sku_id": str(key[1]), "on_hand": str(state["on_hand"]),
                    "occupied": str(state["occupied"]), "reserved": str(state["reserved"]),
                    "hoarded": str(state["hoarded"]), "damaged": str(state["damaged"]),
                })


def replay_inventory(source_override=None, supplemental_count_costs=None):
    return InventoryReplay(source_override, supplemental_count_costs).run()


def validate_baseline(result, replay):
    differences = {"balances": [], "movements": [], "order_lines": [], "orders": [], "holds": [], "damage": [], "calculated": []}
    keys = set(replay.db_balances) | set(result.balances)
    for key in sorted(keys):
        state = result.balance(key)
        row = replay.db_balances.get(key)
        actual = {
            "on_hand": row.on_hand if row else 0, "occupied": row.occupied if row else 0,
            "reserved": row.reserved if row else 0, "hoarded": row.hoarded if row else 0,
            "damaged": row.damaged if row else 0, "cost_amount": row.cost_amount if row else ZERO,
        }
        expected = {name: state[name] for name in actual}
        if expected != actual:
            differences["balances"].append({"warehouse_id": key[0], "sku_id": key[1], "before": actual, "replayed": expected})

    expected_moves = result.movements
    actual_moves = {}
    for row in replay.movements:
        identity = (row.operation_id, row.event_kind, row.entry_key)
        if identity in actual_moves:
            differences["movements"].append({"operation_id": row.operation_id, "entry_key": row.entry_key, "error": "duplicate"})
        source = next((field for field in ("receipt_line", "opening_line", "hold", "sales_order", "split_record", "gift_line", "damage_resolution", "stock_count_line") if getattr(row, f"{field}_id") is not None), None)
        actual_moves[identity] = {
            "key": (row.balance.warehouse_id, row.balance.sku_id), "source": (source, getattr(row, f"{source}_id") if source else None),
            "on_hand_delta": row.on_hand_delta, "occupied_delta": row.occupied_delta,
            "reserved_delta": row.reserved_delta, "hoarded_delta": row.hoarded_delta,
            "damaged_delta": row.damaged_delta, "cost_delta": row.cost_delta,
        }
    for identity in expected_moves.keys() | actual_moves.keys():
        actual, expected = actual_moves.get(identity), expected_moves.get(identity)
        if actual != expected:
            differences["movements"].append({"operation_id": identity[0], "event_kind": identity[1], "entry_key": identity[2], "before": actual, "replayed": expected})

    for line_id in replay.db_order_lines.keys() | result.order_lines.keys():
        row = replay.db_order_lines.get(line_id)
        expected = result.order_lines.get(line_id)
        if row is None or expected is None:
            differences["order_lines"].append({"line_id": line_id, "error": "missing"})
            continue
        actual = {
            "line_id": row.pk, "order_id": row.order_id, "warehouse_id": row.warehouse_id, "sku_id": row.sku_id,
            "quantity": row.quantity, "unit_price": row.unit_price, "revenue_amount": row.revenue_amount,
            "unit_cost_snapshot": row.unit_cost_snapshot, "order_cost_amount": row.order_cost_amount,
            "outbound_freight": row.outbound_freight, "cost_mode": row.cost_mode, "active": row.active,
        }
        if actual != expected:
            differences["order_lines"].append({"line_id": line_id, "before": actual, "replayed": expected})

    for order_id in result.orders:
        row = replay.db_orders.get(order_id)
        if row is None:
            differences["orders"].append({"order_id": order_id, "error": "missing"})
            continue
        expected = result.orders[order_id]
        actual = {name: getattr(row, name) for name in ("revenue_amount", "cost_amount", "margin_amount", "commission_estimate")}
        if actual != {name: expected[name] for name in actual}:
            differences["orders"].append({"order_id": order_id, "before": actual, "replayed": expected})

    for hold_id, expected in result.holds.items():
        if not isinstance(hold_id, int):
            differences["holds"].append({"hold_id": hold_id, "error": "missing_effective_row"})
            continue
        row = replay.hold_by_id.get(hold_id)
        key = (row.balance.warehouse_id, row.balance.sku_id) if row else None
        actual = {"initial_quantity": row.initial_quantity, "remaining_quantity": row.remaining_quantity, "type": row.type, "key": key} if row else None
        desired = {name: expected[name] for name in ("initial_quantity", "remaining_quantity", "type", "key")}
        if actual != desired:
            differences["holds"].append({"hold_id": hold_id, "before": actual, "replayed": desired})
    for damage_id, expected in result.damage.items():
        if not isinstance(damage_id, int):
            differences["damage"].append({"damaged_record_id": damage_id, "error": "missing_effective_row"})
            continue
        row = replay.damage_by_id.get(damage_id)
        key = (row.balance.warehouse_id, row.balance.sku_id) if row else None
        actual = {"original_quantity": row.original_quantity, "remaining_quantity": row.remaining_quantity, "key": key} if row else None
        desired = {name: expected[name] for name in ("original_quantity", "remaining_quantity", "key")}
        if actual != desired:
            differences["damage"].append({"damaged_record_id": damage_id, "before": actual, "replayed": desired})

    for split_id, cost in result.split_costs.items():
        row = replay.split_rows[split_id]
        if row.transferred_cost != cost:
            differences["calculated"].append({"kind": "split", "id": split_id, "before": row.transferred_cost, "replayed": cost})
    for line_id, cost in result.gift_costs.items():
        if replay.gift_lines[line_id].cost_amount != cost:
            differences["calculated"].append({"kind": "gift", "id": line_id, "before": replay.gift_lines[line_id].cost_amount, "replayed": cost})
    for resolution_id, values in result.damage_costs.items():
        row = replay.damage_resolutions[resolution_id]
        if row.deducted_cost != values["cost"] or row.difference != values["difference"]:
            differences["calculated"].append({"kind": "damage", "id": resolution_id, "before": [row.deducted_cost, row.difference], "replayed": [values["cost"], values["difference"]]})
    for line_id, values in result.count_costs.items():
        row = replay.count_lines[line_id]
        if row.delta != values["delta"] or row.cost_delta != values["cost"] or row.effective_unit_cost != values["unit_cost"]:
            differences["calculated"].append({"kind": "stock_count", "id": line_id, "before": [row.delta, row.effective_unit_cost, row.cost_delta], "replayed": [values["delta"], values["unit_cost"], values["cost"]]})
    for operation_id, values in result.fulfillment_costs.items():
        operation = next(row for row in result.operations if row.pk == operation_id)
        recorded = operation.result.get("actual_inventory_cost")
        if recorded is not None and Decimal(recorded) != values["cost"]:
            differences["calculated"].append({"kind": "fulfillment", "id": operation_id, "before": recorded, "replayed": values["cost"]})
    return {name: values for name, values in differences.items() if values}


def run_and_validate(source_override=None, supplemental_count_costs=None):
    replay = InventoryReplay(source_override, supplemental_count_costs)
    result = replay.run()
    differences = validate_baseline(result, replay)
    if differences:
        _fail("现有库存流水无法与完整历史回放一致，纠错未继续。", fields={"baseline_differences": differences})
    return result, replay
