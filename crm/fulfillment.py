from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP, localcontext

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import (
    BusinessOperation,
    CommissionAdjustment,
    CommissionReview,
    InventoryBalance,
    InventoryMovement,
    PaymentAccount,
    PaymentRecord,
    ReceiptImage,
    SalesOrder,
    SalesOrderLine,
    Shipment,
    ShipmentWarehouse,
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
    set_operation_document_id,
)
from .permissions import ROLE_OWNER, ROLE_WAREHOUSE
from .sales import _balance_query, sales_order_commission_visible_to, sales_order_queryset_for


def _version(data):
    return quantity(data.get("expected_version"), "expected_version", positive=True)


def _text(value, field, *, required=False):
    if not isinstance(value, str):
        raise OperationError("文本格式无效。", fields={field: "请输入文本。"})
    result = value.strip()
    if required and not result:
        raise OperationError("请填写必填内容。", fields={field: "此项必填。"})
    if len(result) > 4000:
        raise OperationError("文本超出允许长度。", fields={field: "最多4000个字符。"})
    return result


def _operation_key(data):
    return operation_uuid(data.get("operation_key"))


def _locked_order(actor, order_id, *, changing=False):
    queryset = sales_order_queryset_for(actor, changing=changing)
    order = queryset.select_for_update().filter(pk=identifier(order_id, "order_id")).first()
    if order is None:
        raise OperationError("订单不存在或不在可见范围。", status=404, code="not_found")
    set_operation_document_id(order.pk)
    return order


def _create_operation(actor, order, key, action, input_hash, payload, control):
    return BusinessOperation.objects.create(
        operation_key=key,
        sequence=_next_sequence(control),
        actor=actor,
        action=action,
        input_hash=input_hash,
        payload=payload,
        sales_order=order,
    )


def _finish_operation(operation, order, status=None, **extra):
    result = {
        "document_id": order.pk,
        "order_id": order.pk,
        "sequence": operation.sequence,
        "version": order.version,
    }
    if status is not None:
        result["status"] = status
    result.update(extra)
    operation.result = result
    operation.save(update_fields=("result",))
    return result


def _expected_version(order, expected):
    if order.version != expected:
        raise OperationError("订单已变化，请刷新后重试。", status=409, code="version_conflict")


def _images_for_payment(actor, payment, image_ids):
    existing_ids = set(payment.images.values_list("pk", flat=True)) if payment else set()
    wanted = set(image_ids)
    images = {image.pk: image for image in ReceiptImage.objects.filter(pk__in=wanted)}
    if images.keys() != wanted:
        raise OperationError("图片不存在或已清理。", status=404, code="not_found", fields={"image_ids": "请重新上传图片。"})
    for image in images.values():
        if image.receipt_id is not None or image.payment_id not in (None, payment.pk if payment else None):
            raise OperationError("图片已关联其他单据。", status=409, code="image_already_bound", fields={"image_ids": "请选择未关联图片。"})
        if image.payment_id is None and image.created_at < timezone.now() - timedelta(hours=24):
            raise OperationError("暂存图片已过期，请重新上传。", status=409, code="stale_image")
        if image.owner_id != actor.pk and image.pk not in existing_ids:
            raise OperationError("图片不属于当前操作人。", status=403, code="permission_denied")
    new_count = sum(image.payment_id is None for image in images.values())
    if len(existing_ids) + new_count > 20:
        raise OperationError("每张订单最多保存20张收款图片。", fields={"image_ids": "图片数量超出上限。"})
    return images


def register_payment(actor, order_id, data, request_id=""):
    _require(actor, "register_payment")
    order_id = identifier(order_id, "order_id")
    if not isinstance(data, dict) or set(data) - {
        "operation_key", "expected_version", "account_id", "received_amount", "image_ids", "received_at", "difference_reason",
    }:
        raise OperationError("收款登记数据格式无效。")
    key = _operation_key(data)
    expected_version = _version(data)
    account_id = identifier(data.get("account_id"), "account_id")
    received_amount = amount(data.get("received_amount"), "received_amount")
    image_ids = data.get("image_ids")
    if not isinstance(image_ids, list) or not 1 <= len(image_ids) <= 20:
        raise OperationError("请上传1至20张收款图片。", fields={"image_ids": "至少一张，最多20张。"})
    image_ids = [identifier(item, "image_ids") for item in image_ids]
    if len(set(image_ids)) != len(image_ids):
        raise OperationError("收款图片不能重复。", fields={"image_ids": "请移除重复图片。"})
    difference_reason = _text(data.get("difference_reason", ""), "difference_reason")
    received_at_value = data.get("received_at")
    if received_at_value in (None, ""):
        received_at = None
    elif isinstance(received_at_value, str):
        received_at = parse_datetime(received_at_value)
        if received_at is None:
            raise OperationError("收款时间格式无效。", fields={"received_at": "请输入ISO格式日期时间。"})
        if timezone.is_naive(received_at):
            received_at = timezone.make_aware(received_at, timezone.get_current_timezone())
    else:
        raise OperationError("收款时间格式无效。", fields={"received_at": "请输入ISO格式日期时间。"})

    input_data = {
        "order_id": order_id,
        "expected_version": expected_version,
        "account_id": account_id,
        "received_amount": f"{received_amount:.3f}",
        "image_ids": image_ids,
        "received_at": received_at.isoformat() if received_at else None,
        "difference_reason": difference_reason,
    }
    action = "payment_register"
    input_hash = canonical_hash({"action": action, "input": input_data})

    def write():
        with transaction.atomic():
            control = _lock_control()
            _require(actor, "register_payment")
            order = _locked_order(actor, order_id, changing=True)
            replay = _operation_replay(actor, key, action, input_hash)
            if replay is not None:
                return replay
            _expected_version(order, expected_version)
            if order.status == SalesOrder.Status.CANCELLED:
                raise OperationError("已取消订单不能登记收款。", status=409, code="state_conflict")
            expected_amount = order.revenue_amount
            if received_amount != expected_amount and not difference_reason:
                raise OperationError("登记金额与订单金额不同，请填写原因。", fields={"difference_reason": "金额不一致时必填。"})
            account = PaymentAccount.objects.filter(pk=account_id).first()
            if account is None or not account.is_active:
                raise OperationError("收款账户不存在或已停用。", status=409, code="inactive_reference", fields={"account_id": "请选择启用账户。"})
            payment = PaymentRecord.objects.select_for_update().filter(order=order).first()
            new_payment = payment is None
            images = _images_for_payment(actor, payment, image_ids)
            changed = bool(
                payment is None
                or payment.account_id != account.pk
                or payment.expected_amount != expected_amount
                or payment.received_amount != received_amount
                or payment.received_at != received_at
                or payment.difference_reason != difference_reason
                or any(image.payment_id is None for image in images.values())
            )
            if payment is None:
                payment = PaymentRecord.objects.create(
                    order=order,
                    account=account,
                    registered_by=actor,
                    expected_amount=expected_amount,
                    received_amount=received_amount,
                    expected_payment_version=order.payment_version,
                    received_at=received_at,
                    difference_reason=difference_reason,
                )
            elif changed:
                payment.account = account
                payment.registered_by = actor
                payment.expected_amount = expected_amount
                payment.received_amount = received_amount
                payment.expected_payment_version = order.payment_version
                payment.received_at = received_at
                payment.difference_reason = difference_reason
                payment.status = PaymentRecord.Status.PENDING
                payment.verified_version = None
                payment.problem_reason = ""
                payment.verified_by = None
                payment.verified_at = None
                payment.version += 1
                payment.save()
            if images:
                ReceiptImage.objects.filter(pk__in=images, payment__isnull=True).update(payment=payment)
            if order.status == SalesOrder.Status.AWAITING_PAYMENT:
                order.status = SalesOrder.Status.READY_TO_SHIP
                changed = True
            if changed:
                order.version += 1
                operation = _create_operation(actor, order, key, action, input_hash, input_data, control)
                if new_payment:
                    payment.first_evidence_registered_at = operation.created_at
                    payment.save(update_fields=("first_evidence_registered_at",))
                order.last_sequence = operation.sequence
                order.save(update_fields=("status", "version", "last_sequence", "updated_at"))
            else:
                operation = _create_operation(actor, order, key, action, input_hash, input_data, control)
                order.last_sequence = operation.sequence
                order.version += 1
                order.save(update_fields=("last_sequence", "version", "updated_at"))
            return _finish_operation(
                operation,
                order,
                order.status,
                payment_status=payment.status,
                payment_version=payment.expected_payment_version,
                paused=payment.paused,
            )

    return _run_write(action, actor, request_id, write)


def _can_verify(actor, account):
    roles = set(actor.groups.values_list("name", flat=True))
    if account.responsibility == PaymentAccount.Responsibility.PUBLIC:
        return ROLE_WAREHOUSE in roles or ROLE_OWNER in roles
    return ROLE_OWNER in roles


def verify_payment(actor, order_id, data, request_id=""):
    _require(actor, "verify_payment")
    order_id = identifier(order_id, "order_id")
    if not isinstance(data, dict) or set(data) - {"operation_key", "expected_version", "result", "problem_reason"}:
        raise OperationError("收款核对数据格式无效。")
    key = _operation_key(data)
    expected_version = _version(data)
    result_status = data.get("result")
    if result_status not in (PaymentRecord.Status.PASSED, PaymentRecord.Status.PROBLEM):
        raise OperationError("核对结果无效。", fields={"result": "请选择核对通过或待处理。"})
    problem_reason = _text(data.get("problem_reason", ""), "problem_reason", required=result_status == PaymentRecord.Status.PROBLEM)
    input_data = {"order_id": order_id, "expected_version": expected_version, "result": result_status, "problem_reason": problem_reason}
    action = "payment_verify"
    input_hash = canonical_hash({"action": action, "input": input_data})

    def write():
        with transaction.atomic():
            control = _lock_control()
            _require(actor, "verify_payment")
            order = _locked_order(actor, order_id)
            payment = PaymentRecord.objects.select_for_update().filter(order=order).first()
            if payment is None:
                raise OperationError("订单尚无收款凭证。", status=409, code="state_conflict")
            if not _can_verify(actor, payment.account):
                raise OperationError("当前账号不负责核对该收款账户。", status=403, code="permission_denied")
            replay = _operation_replay(actor, key, action, input_hash)
            if replay is not None:
                return replay
            _expected_version(order, expected_version)
            if payment.expected_payment_version != order.payment_version:
                raise OperationError("收款信息已失效，请先重新登记。", status=409, code="stale_payment")
            payment.status = result_status
            payment.verified_by = actor
            payment.verified_at = timezone.now()
            payment.problem_reason = problem_reason if result_status == PaymentRecord.Status.PROBLEM else ""
            payment.verified_version = order.payment_version
            if result_status == PaymentRecord.Status.PROBLEM:
                if order.status in (SalesOrder.Status.AWAITING_PAYMENT, SalesOrder.Status.READY_TO_SHIP):
                    payment.paused = True
            else:
                payment.paused = False
            payment.version += 1
            payment.save()
            order.version += 1
            operation = _create_operation(actor, order, key, action, input_hash, input_data, control)
            order.last_sequence = operation.sequence
            order.save(update_fields=("version", "last_sequence", "updated_at"))
            return _finish_operation(operation, order, order.status, payment_status=payment.status, paused=payment.paused)

    return _run_write(action, actor, request_id, write)


def _freight_changes(order, data):
    rows = data.get("outbound_freights", [])
    if not isinstance(rows, list):
        raise OperationError("运费格式无效。", fields={"outbound_freights": "请输入明细列表。"})
    active_lines = {line.pk: line for line in order.lines.filter(active=True).select_for_update()}
    changes = {}
    for index, item in enumerate(rows):
        prefix = f"outbound_freights[{index}]"
        if not isinstance(item, dict) or set(item) != {"line_id", "amount"}:
            raise OperationError("运费明细格式无效。", fields={prefix: "每行需要line_id和amount。"})
        line_id = identifier(item["line_id"], f"{prefix}.line_id")
        if line_id not in active_lines:
            raise OperationError("运费关联明细不存在。", status=409, code="state_conflict", fields={f"{prefix}.line_id": "请使用当前订单明细。"})
        if line_id in changes:
            raise OperationError("运费明细不能重复。", fields={prefix: "请移除重复明细。"})
        changes[line_id] = amount(item["amount"], f"{prefix}.amount")
    return active_lines, changes


def _apply_freight(order, active_lines, changes):
    changed = False
    for line_id, value in changes.items():
        line = active_lines[line_id]
        if line.outbound_freight != value:
            line.outbound_freight = value
            line.save(update_fields=("outbound_freight",))
            changed = True
    freight_total = sum((line.outbound_freight for line in active_lines.values()), Decimal(0))
    order.margin_amount = amount(order.revenue_amount - order.cost_amount - freight_total, "margin_amount", nonnegative=False)
    order.commission_estimate = amount(order.margin_amount * Decimal("0.10"), "commission_estimate", nonnegative=False)
    if changed:
        order.amount_version += 1
    return changed


def _payment_for_fulfillment(order):
    payment = PaymentRecord.objects.select_for_update().filter(order=order).first()
    if payment is None or not payment.images.exists():
        raise OperationError("发货前必须登记收款金额和至少一张图片凭证。", status=409, code="payment_required")
    if payment.expected_payment_version != order.payment_version:
        raise OperationError("订单金额变化后须重新登记收款信息。", status=409, code="stale_payment")
    if payment.paused or payment.status == PaymentRecord.Status.PROBLEM:
        raise OperationError("收款问题尚未由对应核对人员确认通过，订单已暂停出库。", status=409, code="payment_paused")
    return payment


def _fulfill_inventory(order, operation):
    lines = list(order.lines.filter(active=True).select_related("warehouse", "sku").order_by("warehouse_id", "sku_id", "id"))
    quantities = {}
    for line in lines:
        key = (line.warehouse_id, line.sku_id)
        quantities[key] = quantities.get(key, 0) + line.quantity
    balances = {
        (balance.warehouse_id, balance.sku_id): balance
        for balance in InventoryBalance.objects.select_for_update()
        .filter(_balance_query(quantities))
        .order_by("warehouse_id", "sku_id", "id")
    }
    if balances.keys() != quantities.keys():
        raise OperationError("订单库存占用记录不完整，请联系管理员。", status=409, code="state_conflict")
    deductions = {}
    for key, requested in quantities.items():
        balance = balances[key]
        if balance.on_hand < requested or balance.occupied < requested:
            raise OperationError("订单库存占用与在库数量不一致，请联系管理员。", status=409, code="stock_conflict")
        if requested == balance.on_hand:
            cost = balance.cost_amount
        else:
            with localcontext() as context:
                context.prec = 50
                cost = (balance.cost_amount * requested / balance.on_hand).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        _check_balance_result(
            balance,
            on_hand=balance.on_hand - requested,
            occupied=balance.occupied - requested,
            cost=balance.cost_amount - cost,
        )
        deductions[key] = cost

    for key in sorted(quantities):
        balance = balances[key]
        outgoing = quantities[key]
        cost = deductions[key]
        balance.on_hand -= outgoing
        balance.occupied -= outgoing
        balance.cost_amount -= cost
        if balance.on_hand == 0:
            balance.cost_amount = Decimal("0.000")
        balance.version += 1
        balance.quantity_version += 1
        balance.save(update_fields=("on_hand", "occupied", "cost_amount", "version", "quantity_version"))
        InventoryMovement.objects.create(
            operation=operation,
            balance=balance,
            sales_order=order,
            event_kind="order_outbound",
            entry_key=f"warehouse:{balance.warehouse_id}:sku:{balance.sku_id}",
            on_hand_delta=-outgoing,
            occupied_delta=-outgoing,
            cost_delta=-cost,
            cost_revision=balance.version,
        )
    return sum(quantities.values()), sum(deductions.values(), Decimal(0))


def _normalise_shipments(data):
    rows = data.get("shipments")
    if not isinstance(rows, list) or not rows:
        raise OperationError("请填写至少一条物流信息。", fields={"shipments": "至少需要一条物流信息。"})
    normalized = []
    assigned = set()
    for index, item in enumerate(rows):
        prefix = f"shipments[{index}]"
        if not isinstance(item, dict) or set(item) != {"carrier", "tracking_number", "warehouse_ids"}:
            raise OperationError("物流信息格式无效。", fields={prefix: "每行需要carrier、tracking_number和warehouse_ids。"})
        carrier = item["carrier"]
        if carrier not in Shipment.Carrier.values:
            raise OperationError("承运方无效。", fields={f"{prefix}.carrier": "请选择跨越、顺丰或货拉拉。"})
        tracking = _text(item["tracking_number"], f"{prefix}.tracking_number", required=True)
        if len(tracking) > 255:
            raise OperationError("物流编号超出长度。", fields={f"{prefix}.tracking_number": "最多255个字符。"})
        warehouse_ids = item["warehouse_ids"]
        if not isinstance(warehouse_ids, list) or not warehouse_ids:
            raise OperationError("每条物流信息至少关联一个出货仓库。", fields={f"{prefix}.warehouse_ids": "请选择仓库。"})
        ids = [identifier(value, f"{prefix}.warehouse_ids") for value in warehouse_ids]
        if len(set(ids)) != len(ids) or assigned.intersection(ids):
            raise OperationError("每个出货仓库只能关联一条物流信息。", fields={f"{prefix}.warehouse_ids": "仓库不能重复关联。"})
        assigned.update(ids)
        normalized.append({"carrier": carrier, "tracking_number": tracking, "warehouse_ids": ids})
    return normalized, assigned


def _perform_fulfillment(actor, order, key, action, input_data, input_hash, control, *, onsite):
    expected_version = input_data["expected_version"]
    _expected_version(order, expected_version)
    if order.status != SalesOrder.Status.READY_TO_SHIP:
        raise OperationError("订单当前状态不能交付。", status=409, code="state_conflict")
    if onsite != (order.delivery_mode == SalesOrder.DeliveryMode.ON_SITE):
        raise OperationError("交付方式与订单不一致。", status=409, code="state_conflict")
    _payment_for_fulfillment(order)
    active_lines, freight_changes = _freight_changes(order, input_data)
    shipments, assigned_warehouses = _normalise_shipments(input_data) if not onsite else ([], set())
    if not onsite:
        selected_warehouses = {line.warehouse_id for line in active_lines.values()}
        if assigned_warehouses != selected_warehouses:
            raise OperationError("所有供货仓库都必须且只能关联到本次物流。", status=409, code="warehouse_coverage", fields={"shipments": "只能选择订单中已有供货仓库，并覆盖全部供货仓库。"})
    _apply_freight(order, active_lines, freight_changes)
    operation = _create_operation(actor, order, key, action, input_hash, input_data, control)
    quantity_out, actual_cost = _fulfill_inventory(order, operation)
    if onsite:
        order.status = SalesOrder.Status.COMPLETED
        order.completed_at = timezone.now()
    else:
        for row in shipments:
            shipment = Shipment.objects.create(
                order=order,
                carrier=row["carrier"],
                tracking_number=row["tracking_number"],
                created_by=actor,
            )
            ShipmentWarehouse.objects.bulk_create([
                ShipmentWarehouse(shipment=shipment, warehouse_id=warehouse_id)
                for warehouse_id in row["warehouse_ids"]
            ])
        order.status = SalesOrder.Status.SHIPPED
        order.shipped_at = timezone.now()
    order.version += 1
    order.last_sequence = operation.sequence
    order.save(update_fields=(
        "status", "completed_at", "shipped_at", "version", "amount_version", "last_sequence",
        "margin_amount", "commission_estimate", "updated_at",
    ))
    return _finish_operation(
        operation,
        order,
        order.status,
        quantity_out=quantity_out,
        actual_inventory_cost=f"{actual_cost:.3f}",
        amount_version=order.amount_version,
    )


def ship_sales_order(actor, order_id, data, request_id=""):
    _require(actor, "ship_salesorder")
    order_id = identifier(order_id, "order_id")
    if not isinstance(data, dict) or set(data) - {"operation_key", "expected_version", "shipments", "outbound_freights"}:
        raise OperationError("发货数据格式无效。")
    key = _operation_key(data)
    normalized_shipments, _ = _normalise_shipments(data)
    normalized = {
        "expected_version": _version(data),
        "shipments": normalized_shipments,
        "outbound_freights": data.get("outbound_freights", []),
    }
    action = "sales_order_ship"
    input_hash = canonical_hash({"action": action, "order_id": order_id, "input": normalized})

    def write():
        with transaction.atomic():
            control = _lock_control()
            _require(actor, "ship_salesorder")
            order = _locked_order(actor, order_id)
            replay = _operation_replay(actor, key, action, input_hash)
            if replay is not None:
                return replay
            return _perform_fulfillment(actor, order, key, action, normalized, input_hash, control, onsite=False)

    return _run_write(action, actor, request_id, write)


def deliver_on_site(actor, order_id, data, request_id=""):
    _require(actor, "deliver_on_site")
    order_id = identifier(order_id, "order_id")
    if not isinstance(data, dict) or set(data) - {"operation_key", "expected_version", "outbound_freights"}:
        raise OperationError("现场交付数据格式无效。")
    key = _operation_key(data)
    normalized = {"expected_version": _version(data), "outbound_freights": data.get("outbound_freights", [])}
    action = "sales_order_onsite_delivery"
    input_hash = canonical_hash({"action": action, "order_id": order_id, "input": normalized})

    def write():
        with transaction.atomic():
            control = _lock_control()
            _require(actor, "deliver_on_site")
            order = _locked_order(actor, order_id)
            replay = _operation_replay(actor, key, action, input_hash)
            if replay is not None:
                return replay
            return _perform_fulfillment(actor, order, key, action, normalized, input_hash, control, onsite=True)

    return _run_write(action, actor, request_id, write)


def receive_sales_order(actor, order_id, data, request_id=""):
    _require(actor, "confirm_salesorder_receipt")
    order_id = identifier(order_id, "order_id")
    if not isinstance(data, dict) or set(data) - {"operation_key", "expected_version", "outbound_freights"}:
        raise OperationError("收货确认数据格式无效。")
    key = _operation_key(data)
    expected_version = _version(data)
    input_data = {"expected_version": expected_version, "outbound_freights": data.get("outbound_freights", [])}
    action = "sales_order_receive"
    input_hash = canonical_hash({"action": action, "order_id": order_id, "input": input_data})

    def write():
        with transaction.atomic():
            control = _lock_control()
            _require(actor, "confirm_salesorder_receipt")
            order = _locked_order(actor, order_id)
            if actor.pk != order.salesperson_id:
                raise OperationError("只有订单所属销售可以确认收货。", status=403, code="permission_denied")
            replay = _operation_replay(actor, key, action, input_hash)
            if replay is not None:
                return replay
            _expected_version(order, expected_version)
            if order.delivery_mode != SalesOrder.DeliveryMode.SHIPMENT or order.status != SalesOrder.Status.SHIPPED:
                raise OperationError("只有已发货的快递订单可以确认收货。", status=409, code="state_conflict")
            active_lines, freight_changes = _freight_changes(order, input_data)
            _apply_freight(order, active_lines, freight_changes)
            order.status = SalesOrder.Status.COMPLETED
            order.completed_at = timezone.now()
            order.version += 1
            operation = _create_operation(actor, order, key, action, input_hash, input_data, control)
            order.last_sequence = operation.sequence
            order.save(update_fields=("status", "completed_at", "version", "amount_version", "margin_amount", "commission_estimate", "last_sequence", "updated_at"))
            return _finish_operation(operation, order, order.status, amount_version=order.amount_version)

    return _run_write(action, actor, request_id, write)


def update_outbound_freight(actor, order_id, data, request_id=""):
    order_id = identifier(order_id, "order_id")
    if not isinstance(data, dict) or set(data) - {"operation_key", "expected_version", "outbound_freights"}:
        raise OperationError("运费数据格式无效。")
    key = _operation_key(data)
    expected_version = _version(data)
    outbound_freights = data.get("outbound_freights")
    input_data = {"expected_version": expected_version, "outbound_freights": outbound_freights}
    action = "sales_order_freight"
    input_hash = canonical_hash({"action": action, "order_id": order_id, "input": input_data})

    def write():
        with transaction.atomic():
            control = _lock_control()
            order = _locked_order(actor, order_id)
            permission = "deliver_on_site" if order.delivery_mode == SalesOrder.DeliveryMode.ON_SITE else "ship_salesorder"
            _require(actor, permission)
            replay = _operation_replay(actor, key, action, input_hash)
            if replay is not None:
                return replay
            _expected_version(order, expected_version)
            if order.status not in (SalesOrder.Status.SHIPPED, SalesOrder.Status.COMPLETED):
                raise OperationError("完成交付后才可补录运费。", status=409, code="state_conflict")
            active_lines, changes = _freight_changes(order, input_data)
            if not changes:
                raise OperationError("请至少提交一项运费。", fields={"outbound_freights": "请输入需要修改的明细。"})
            _apply_freight(order, active_lines, changes)
            order.version += 1
            operation = _create_operation(actor, order, key, action, input_hash, input_data, control)
            order.last_sequence = operation.sequence
            order.save(update_fields=("version", "amount_version", "margin_amount", "commission_estimate", "last_sequence", "updated_at"))
            return _finish_operation(operation, order, order.status, amount_version=order.amount_version)

    return _run_write(action, actor, request_id, write)


def _commission_review_result_for(actor, order, result):
    if sales_order_commission_visible_to(actor, order):
        return result
    hidden_fields = {"approved_amount", "first_earned_at", "adjustment_id", "delta"}
    return {key: value for key, value in result.items() if key not in hidden_fields}


def review_commission(actor, order_id, data, request_id=""):
    _require(actor, "review_commission")
    order_id = identifier(order_id, "order_id")
    if not isinstance(data, dict) or set(data) - {"operation_key", "expected_version"}:
        raise OperationError("提成复核数据格式无效。")
    key = _operation_key(data)
    expected_version = _version(data)
    input_data = {"order_id": order_id, "expected_version": expected_version}
    action = "commission_review"
    input_hash = canonical_hash({"action": action, "input": input_data})

    def write():
        with transaction.atomic():
            control = _lock_control()
            _require(actor, "review_commission")
            order = _locked_order(actor, order_id)
            replay = _operation_replay(actor, key, action, input_hash)
            if replay is not None:
                return _commission_review_result_for(actor, order, replay)
            _expected_version(order, expected_version)
            if order.status != SalesOrder.Status.COMPLETED:
                raise OperationError("订单完成后才能复核提成。", status=409, code="state_conflict")
            payment = PaymentRecord.objects.select_for_update().filter(order=order).first()
            if (
                payment is None
                or payment.status != PaymentRecord.Status.PASSED
                or payment.paused
                or payment.expected_payment_version != order.payment_version
                or payment.verified_version != order.payment_version
            ):
                raise OperationError("订单收款尚未按当前付款版本核对通过。", status=409, code="payment_unverified")
            review = CommissionReview.objects.select_for_update().filter(order=order).first()
            if review and review.approved_amount_version == order.amount_version:
                raise OperationError("当前金额版本已复核，无需重复复核。", status=409, code="already_reviewed")
            now = timezone.now()
            previous_amount = review.approved_amount if review and review.approved_amount is not None else Decimal("0.000")
            kind = CommissionAdjustment.Kind.REREVIEW if review and review.approved_amount is not None else CommissionAdjustment.Kind.INITIAL
            if review is None:
                review = CommissionReview.objects.create(order=order)
            if review.first_earned_at is None:
                review.first_earned_at = now
            review.approved_amount = order.commission_estimate
            review.approved_amount_version = order.amount_version
            review.reviewer = actor
            review.reviewed_at = now
            review.save()
            order.version += 1
            operation = _create_operation(actor, order, key, action, input_hash, input_data, control)
            order.last_sequence = operation.sequence
            order.save(update_fields=("version", "last_sequence", "updated_at"))
            delta = review.approved_amount - previous_amount
            adjustment = CommissionAdjustment.objects.create(
                approval_operation=operation,
                order=order,
                kind=kind,
                old_amount=previous_amount,
                new_amount=review.approved_amount,
                delta=delta,
                confirmed_at=now,
            )
            result_fields = {"amount_version": review.approved_amount_version}
            if sales_order_commission_visible_to(actor, order):
                result_fields.update({
                    "approved_amount": f"{review.approved_amount:.3f}",
                    "first_earned_at": review.first_earned_at.isoformat(),
                    "adjustment_id": adjustment.pk,
                    "delta": f"{delta:.3f}",
                })
            return _finish_operation(
                operation,
                order,
                order.status,
                **result_fields,
            )

    return _run_write(action, actor, request_id, write)


def complete_commission_adjustment(actor, adjustment_id, data, request_id=""):
    _require(actor, "complete_commission_adjustment")
    adjustment_id = identifier(adjustment_id, "adjustment_id")
    if not isinstance(data, dict) or set(data) - {"operation_key", "expected_version"}:
        raise OperationError("提成差额登记数据格式无效。")
    key = _operation_key(data)
    expected_version = _version(data)
    input_data = {"adjustment_id": adjustment_id, "expected_version": expected_version}
    action = "commission_adjustment_complete"
    input_hash = canonical_hash({"action": action, "input": input_data})

    def write():
        with transaction.atomic():
            control = _lock_control()
            _require(actor, "complete_commission_adjustment")
            order_id = CommissionAdjustment.objects.filter(pk=adjustment_id).values_list("order_id", flat=True).first()
            if order_id is None:
                raise OperationError("提成差额记录不存在。", status=404, code="not_found")
            order = _locked_order(actor, order_id)
            adjustment = CommissionAdjustment.objects.select_for_update().filter(pk=adjustment_id, order=order).first()
            if adjustment is None:
                raise OperationError("提成差额记录不存在。", status=404, code="not_found")
            if actor.pk != order.salesperson_id:
                raise OperationError("只有该订单销售可以登记线下处理完成。", status=403, code="permission_denied")
            replay = _operation_replay(actor, key, action, input_hash)
            if replay is not None:
                return replay
            _expected_version(order, expected_version)
            if adjustment.completed_at is not None:
                raise OperationError("该差额已登记完成。", status=409, code="state_conflict")
            now = timezone.now()
            adjustment.completed_by = actor
            adjustment.completed_at = now
            adjustment.save(update_fields=("completed_by", "completed_at"))
            order.version += 1
            operation = _create_operation(actor, order, key, action, input_hash, input_data, control)
            order.last_sequence = operation.sequence
            order.save(update_fields=("version", "last_sequence", "updated_at"))
            return _finish_operation(operation, order, order.status, adjustment_id=adjustment.pk, completed_at=now.isoformat())

    return _run_write(action, actor, request_id, write)
