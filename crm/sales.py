from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP, localcontext

from django.core import signing
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    AccountProfile,
    BusinessControl,
    BusinessOperation,
    Channel,
    Customer,
    CustomerAddress,
    InventoryBalance,
    InventoryMovement,
    SKU,
    SalesOrder,
    SalesOrderLine,
    Warehouse,
)
from .operations import (
    OperationError,
    _check_balance_result,
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
    set_operation_document_id,
)
from .permissions import ROLE_OWNER, ROLE_SALES
from .regions import valid_region_pair

SALES_ORDER_PREVIEW_SALT = "crm.sales-order-preview.v1"


def customer_queryset_for(actor, *, changing=False):
    all_codename = "change_all_customers" if changing else "view_all_customers"
    queryset = Customer.objects.all()
    if actor.is_superuser or actor.has_perm(f"crm.{all_codename}"):
        return queryset
    return queryset.filter(owner_id=actor.pk)


def sales_order_queryset_for(actor, *, changing=False):
    all_codename = "change_all_sales_orders" if changing else "view_all_sales_orders"
    queryset = SalesOrder.objects.all()
    if actor.is_superuser or actor.has_perm(f"crm.{all_codename}"):
        return queryset
    return queryset.filter(salesperson_id=actor.pk)


def _text(value, field, *, required=False, max_length=None):
    if not isinstance(value, str):
        if value is None and not required:
            return ""
        raise OperationError("文本格式无效。", fields={field: "请输入文本。"})
    result = value.strip()
    if required and not result:
        raise OperationError("请填写必填内容。", fields={field: "此项必填。"})
    if max_length and len(result) > max_length:
        raise OperationError("文本超出允许长度。", fields={field: f"最多{max_length}个字符。"})
    return result


def _normalize_aliases(value, field="aliases"):
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise OperationError("别名格式无效。", fields={field: "请输入文本列表。"})
    return list(dict.fromkeys(item.strip() for item in value if item.strip()))


def _normalize_birthday(value, field="birthday"):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        raise OperationError("生日格式无效。", fields={field: "请使用YYYY-MM-DD。"})
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise OperationError("生日格式无效。", fields={field: "请使用YYYY-MM-DD。"})
    try:
        normalized = date.fromisoformat(value)
    except ValueError:
        raise OperationError("生日格式无效。", fields={field: "请使用YYYY-MM-DD。"}) from None
    if normalized.isoformat() != value:
        raise OperationError("生日格式无效。", fields={field: "请使用YYYY-MM-DD。"})
    return normalized


def _normalize_addresses(value):
    if not isinstance(value, list) or not value:
        raise OperationError("客户至少需要一个完整地址。", fields={"addresses": "请填写至少一个完整地址。"})
    result = []
    seen_ids = set()
    for index, item in enumerate(value):
        prefix = f"addresses[{index}]"
        if not isinstance(item, dict):
            raise OperationError("地址格式无效。", fields={prefix: "地址须为对象。"})
        unknown = set(item) - {"id", "recipient", "phone", "province", "city", "detail"}
        if unknown:
            raise OperationError("地址包含不支持的字段。", fields={prefix: "请刷新后重试。"})
        address_id = item.get("id")
        if address_id not in (None, ""):
            address_id = identifier(address_id, f"{prefix}.id")
            if address_id in seen_ids:
                raise OperationError("同一地址不能重复提交。", fields={f"{prefix}.id": "地址重复。"})
            seen_ids.add(address_id)
        address = {
            "id": address_id,
            "recipient": _text(item.get("recipient"), f"{prefix}.recipient", required=True, max_length=120),
            "contact": _text(item.get("phone"), f"{prefix}.phone", required=True),
            "province": _text(item.get("province"), f"{prefix}.province", required=True, max_length=80),
            "city": _text(item.get("city"), f"{prefix}.city", required=True, max_length=80),
            "detail": _text(item.get("detail"), f"{prefix}.detail", required=True),
        }
        if not valid_region_pair(address["province"], address["city"]):
            raise OperationError("省市不匹配或不在中国大陆列表中。", fields={f"{prefix}.city": "请选择该省份下的城市。"})
        result.append(address)
    return result


def _normalize_customer_fields(data, *, creating=False):
    if not isinstance(data, dict):
        raise OperationError("客户资料格式无效。")
    allowed = {"name", "contact", "company", "aliases", "birthday", "interests", "hobbies", "addresses"}
    if creating:
        allowed |= {"owner_id"}
    unknown = set(data) - allowed
    if unknown:
        raise OperationError("客户资料包含不支持的字段。", fields={next(iter(sorted(unknown))): "此字段不能修改。"})
    values = {}
    if creating or "name" in data:
        values["name"] = _text(data.get("name"), "name", required=True, max_length=200)
    if creating or "contact" in data:
        values["contact"] = _text(data.get("contact"), "contact", required=True)
    for field, limit in (("company", 200), ("interests", None), ("hobbies", None)):
        if field in data or creating:
            values[field] = _text(data.get(field), field, max_length=limit)
    if "aliases" in data or creating:
        values["aliases"] = _normalize_aliases(data.get("aliases", []))
    if "birthday" in data or creating:
        values["birthday"] = _normalize_birthday(data.get("birthday"))
    if "addresses" in data or creating:
        values["addresses"] = _normalize_addresses(data.get("addresses"))
    return values


def _customer_owner(actor, requested_owner_id):
    is_sales = actor.groups.filter(name=ROLE_SALES).exists()
    if is_sales:
        if requested_owner_id not in (None, "", actor.pk, str(actor.pk)):
            raise OperationError("销售人员只能为本人建立客户档案。", status=403, code="permission_denied")
        return actor
    if requested_owner_id in (None, ""):
        raise OperationError("请指定负责该客户的销售人员。", fields={"owner_id": "此项必填。"})
    owner_id = identifier(requested_owner_id, "owner_id")
    owner = actor.__class__.objects.filter(pk=owner_id, is_active=True, groups__name=ROLE_SALES).first()
    if owner is None:
        raise OperationError("请选择启用中的销售人员。", status=409, code="inactive_reference", fields={"owner_id": "销售人员不存在或未启用。"})
    return owner


def _save_customer_addresses(customer, addresses):
    kept_ids = set()
    for item in addresses:
        address_id = item.pop("id")
        if address_id is None:
            address = CustomerAddress.objects.create(customer=customer, **item)
        else:
            address = CustomerAddress.objects.select_for_update().filter(pk=address_id, customer=customer).first()
            if address is None:
                raise OperationError("地址不属于该客户。", status=409, code="version_conflict", fields={"addresses": "请刷新客户资料后重试。"})
            for field, value in item.items():
                setattr(address, field, value)
            address.is_active = True
            address.save(update_fields=(*item.keys(), "is_active"))
        kept_ids.add(address.pk)
    customer.addresses.exclude(pk__in=kept_ids).filter(is_active=True).update(is_active=False)


def create_customer(actor, data, operation_key, request_id=""):
    _require(actor, "add_customer")
    values = _normalize_customer_fields(data, creating=True)
    owner_value = data.get("owner_id")
    if actor.groups.filter(name=ROLE_SALES).exists():
        normalized_owner_id = actor.pk if owner_value in (None, "", actor.pk, str(actor.pk)) else owner_value
    else:
        normalized_owner_id = identifier(owner_value, "owner_id") if owner_value not in (None, "") else None
    key = operation_uuid(operation_key)
    input_hash = canonical_hash({"action": "customer_create", "input": {**values, "owner_id": normalized_owner_id}})

    def write():
        with transaction.atomic():
            control = _lock_control()
            _require(actor, "add_customer")
            replay = _operation_replay(actor, key, "customer_create", input_hash)
            if replay is not None:
                return replay
            owner = _customer_owner(actor, owner_value)
            addresses = values.pop("addresses")
            customer = Customer.objects.create(owner=owner, **values)
            _save_customer_addresses(customer, addresses)
            sequence = _next_sequence(control)
            set_operation_document_id(customer.pk)
            operation = BusinessOperation.objects.create(
                operation_key=key,
                sequence=sequence,
                actor=actor,
                action="customer_create",
                input_hash=input_hash,
                payload={"customer_id": customer.pk, "owner_id": owner.pk, "address_count": len(addresses)},
                customer=customer,
            )
            result = {"customer_id": customer.pk, "version": customer.version, "sequence": sequence}
            operation.result = result
            operation.save(update_fields=("result",))
            return result

    return _run_write("customer_create", actor, request_id, write)


def update_customer(actor, customer_id, data, request_id=""):
    _require(actor, "change_customer")
    customer_id = identifier(customer_id, "customer_id")
    if not isinstance(data, dict):
        raise OperationError("客户资料格式无效。")
    expected_version = quantity(data.get("expected_version"), "expected_version", positive=True)
    key = operation_uuid(data.get("operation_key"))
    payload = {field: value for field, value in data.items() if field not in ("operation_key", "expected_version")}
    values = _normalize_customer_fields(payload)
    if not values:
        raise OperationError("请至少修改一个客户资料字段。")
    input_hash = canonical_hash({"action": "customer_update", "customer_id": customer_id, "expected_version": expected_version, "input": values})

    def write():
        with transaction.atomic():
            control = _lock_control()
            _require(actor, "change_customer")
            replay = _operation_replay(actor, key, "customer_update", input_hash)
            if replay is not None:
                return replay
            customer = customer_queryset_for(actor, changing=True).select_for_update().filter(pk=customer_id).first()
            if customer is None:
                raise OperationError("客户不存在或不在可维护范围。", status=404, code="not_found")
            set_operation_document_id(customer.pk)
            if customer.version != expected_version:
                raise OperationError("客户资料已变化，请刷新后重试。", status=409, code="version_conflict")
            if not customer.is_active:
                raise OperationError("已归档客户不能直接修改。", status=409, code="state_conflict")
            addresses = values.pop("addresses", None)
            for field, value in values.items():
                setattr(customer, field, value)
            if addresses is not None:
                _save_customer_addresses(customer, addresses)
            customer.version += 1
            customer.save()
            sequence = _next_sequence(control)
            operation = BusinessOperation.objects.create(
                operation_key=key,
                sequence=sequence,
                actor=actor,
                action="customer_update",
                input_hash=input_hash,
                payload={"customer_id": customer.pk, "expected_version": expected_version, "changed_fields": sorted(values), "addresses_changed": addresses is not None},
                customer=customer,
            )
            result = {"customer_id": customer.pk, "version": customer.version, "sequence": sequence}
            operation.result = result
            operation.save(update_fields=("result",))
            return result

    return _run_write("customer_update", actor, request_id, write)


def archive_customer(actor, customer_id, data, request_id=""):
    _require(actor, "archive_customer")
    customer_id = identifier(customer_id, "customer_id")
    if not isinstance(data, dict):
        raise OperationError("客户归档数据格式无效。")
    if set(data) - {"expected_version", "operation_key"}:
        raise OperationError("客户归档数据包含不支持的字段。")
    expected_version = quantity(data.get("expected_version"), "expected_version", positive=True)
    key = operation_uuid(data.get("operation_key"))
    input_hash = canonical_hash({"action": "customer_archive", "customer_id": customer_id, "expected_version": expected_version})

    def write():
        with transaction.atomic():
            control = _lock_control()
            _require(actor, "archive_customer")
            replay = _operation_replay(actor, key, "customer_archive", input_hash)
            if replay is not None:
                return replay
            customer = customer_queryset_for(actor, changing=True).select_for_update().filter(pk=customer_id).first()
            if customer is None:
                raise OperationError("客户不存在或不在可维护范围。", status=404, code="not_found")
            set_operation_document_id(customer.pk)
            if customer.version != expected_version or not customer.is_active:
                raise OperationError("客户状态已变化，请刷新后重试。", status=409, code="version_conflict")
            customer.is_active = False
            customer.version += 1
            customer.save(update_fields=("is_active", "version", "updated_at"))
            sequence = _next_sequence(control)
            operation = BusinessOperation.objects.create(
                operation_key=key,
                sequence=sequence,
                actor=actor,
                action="customer_archive",
                input_hash=input_hash,
                payload={"customer_id": customer.pk, "expected_version": expected_version},
                customer=customer,
            )
            result = {"customer_id": customer.pk, "version": customer.version, "is_active": False, "sequence": sequence}
            operation.result = result
            operation.save(update_fields=("result",))
            return result

    return _run_write("customer_archive", actor, request_id, write)


def transfer_customer(actor, customer_id, data, request_id=""):
    _require(actor, "change_customer_owner")
    if not actor.is_superuser and not actor.groups.filter(name=ROLE_OWNER).exists():
        raise OperationError("只有老板或管理员可以转交客户。", status=403, code="permission_denied")
    customer_id = identifier(customer_id, "customer_id")
    if not isinstance(data, dict):
        raise OperationError("客户转交数据格式无效。")
    if set(data) - {"owner_id", "expected_version", "operation_key"}:
        raise OperationError("客户转交数据包含不支持的字段。")
    expected_version = quantity(data.get("expected_version"), "expected_version", positive=True)
    owner_id = identifier(data.get("owner_id"), "owner_id")
    key = operation_uuid(data.get("operation_key"))
    input_hash = canonical_hash({"action": "customer_transfer", "customer_id": customer_id, "owner_id": owner_id, "expected_version": expected_version})

    def write():
        with transaction.atomic():
            control = _lock_control()
            _require(actor, "change_customer_owner")
            if not actor.is_superuser and not actor.groups.filter(name=ROLE_OWNER).exists():
                raise OperationError("只有老板或管理员可以转交客户。", status=403, code="permission_denied")
            replay = _operation_replay(actor, key, "customer_transfer", input_hash)
            if replay is not None:
                return replay
            customer = Customer.objects.select_for_update().filter(pk=customer_id).first()
            if customer is None:
                raise OperationError("客户不存在。", status=404, code="not_found")
            owner = actor.__class__.objects.filter(pk=owner_id, is_active=True, groups__name=ROLE_SALES).first()
            if owner is None:
                raise OperationError("请选择启用中的销售人员。", status=409, code="inactive_reference")
            set_operation_document_id(customer.pk)
            if customer.version != expected_version:
                raise OperationError("客户资料已变化，请刷新后重试。", status=409, code="version_conflict")
            customer.owner = owner
            customer.version += 1
            customer.save(update_fields=("owner", "version", "updated_at"))
            sequence = _next_sequence(control)
            operation = BusinessOperation.objects.create(
                operation_key=key,
                sequence=sequence,
                actor=actor,
                action="customer_transfer",
                input_hash=input_hash,
                payload={"customer_id": customer.pk, "owner_id": owner.pk, "expected_version": expected_version},
                customer=customer,
            )
            result = {"customer_id": customer.pk, "owner_id": owner.pk, "version": customer.version, "sequence": sequence}
            operation.result = result
            operation.save(update_fields=("result",))
            return result

    return _run_write("customer_transfer", actor, request_id, write)


def update_birthday_preference(actor, data):
    if not isinstance(data, dict):
        raise OperationError("个人设置格式无效。")
    if set(data) - {"birthday_reminder_enabled", "expected_version", "operation_key"}:
        raise OperationError("个人设置包含不支持的字段。")
    enabled = data.get("birthday_reminder_enabled")
    if not isinstance(enabled, bool):
        raise OperationError("生日提醒开关格式无效。", fields={"birthday_reminder_enabled": "请输入布尔值。"})
    expected_version = quantity(data.get("expected_version"), "expected_version", positive=True)
    key = operation_uuid(data.get("operation_key"))
    input_hash = canonical_hash({"action": "birthday_preference", "enabled": enabled, "expected_version": expected_version})
    with transaction.atomic():
        profile, _ = AccountProfile.objects.get_or_create(user=actor)
        profile = AccountProfile.objects.select_for_update().get(pk=profile.pk)
        replay = _operation_replay(actor, key, "birthday_preference", input_hash)
        if replay is not None:
            return replay
        if profile.version != expected_version:
            raise OperationError("个人设置已变化，请刷新后重试。", status=409, code="version_conflict")
        if profile.birthday_reminder_enabled != enabled:
            profile.birthday_reminder_enabled = enabled
            profile.version += 1
            profile.save(update_fields=("birthday_reminder_enabled", "version"))
        operation = BusinessOperation.objects.create(
            operation_key=key,
            sequence=None,
            actor=actor,
            action="birthday_preference",
            input_hash=input_hash,
            payload={"account_profile_id": profile.pk, "birthday_reminder_enabled": enabled},
            account_profile=profile,
        )
        result = {"birthday_reminder_enabled": profile.birthday_reminder_enabled, "version": profile.version}
        operation.result = result
        operation.save(update_fields=("result",))
        return result


def _optional_identifier(value, field):
    return None if value in (None, "") else identifier(value, field)


def _normalize_order_input(data, order=None):
    if not isinstance(data, dict):
        raise OperationError("订单数据格式无效。")
    allowed = {
        "customer_id", "customer_address_id", "channel_id", "delivery_mode", "address_snapshot",
        "remarks", "lines", "expected_version", "order_id",
    }
    unknown = set(data) - allowed
    if unknown:
        raise OperationError("订单包含不支持的字段。", fields={next(iter(sorted(unknown))): "此字段不能修改。"})
    field = lambda name, current=None: data[name] if name in data else current
    current_address = {
        "recipient": order.recipient_snapshot,
        "contact": order.contact_snapshot,
        "province": order.province_snapshot,
        "city": order.city_snapshot,
        "detail": order.detail_snapshot,
    } if order else {}
    customer_id = _optional_identifier(field("customer_id", order.customer_id if order else None), "customer_id")
    address_id = _optional_identifier(data.get("customer_address_id"), "customer_address_id")
    if "order_id" in data:
        supplied_order_id = _optional_identifier(data.get("order_id"), "order_id")
        if supplied_order_id != (order.pk if order else None):
            raise OperationError("订单预览对应的订单编号无效。", status=409, code="stale_preview")
    channel_id = identifier(field("channel_id", order.channel_id if order else None), "channel_id")
    delivery_mode = field("delivery_mode", order.delivery_mode if order else None)
    if delivery_mode not in SalesOrder.DeliveryMode.values:
        raise OperationError("交付方式无效。", fields={"delivery_mode": "请选择快递或现场。"})
    if address_id is not None and customer_id is None:
        raise OperationError("未选择客户，不能使用客户地址。", fields={"customer_address_id": "请先选择客户。"})
    address_value = data.get("address_snapshot", {} if address_id is not None else current_address)
    if not isinstance(address_value, dict):
        raise OperationError("收件地址格式无效。", fields={"address_snapshot": "请填写收件地址对象。"})
    if address_id is not None and any(address_value.values()):
        raise OperationError("客户地址与手工快照不能同时提交。", fields={"address_snapshot": "请选择一种收件地址来源。"})
    address_fields = ("recipient", "contact", "province", "city", "detail")
    if set(address_value) - set(address_fields):
        raise OperationError("收件地址包含不支持的字段。", fields={"address_snapshot": "请刷新后重试。"})
    address_snapshot = {
        name: _text(address_value.get(name), f"address_snapshot.{name}", max_length=120 if name == "recipient" else 80 if name in ("province", "city") else None)
        for name in address_fields
    }
    lines_value = data.get("lines")
    if not isinstance(lines_value, list) or not lines_value:
        raise OperationError("订单至少需要一个商品明细。", fields={"lines": "请添加至少一个商品。"})
    if len(lines_value) > 200:
        raise OperationError("订单明细数量超出单次提交上限。", fields={"lines": "单次最多提交200行。"})
    lines = []
    seen_line_ids = set()
    for index, item in enumerate(lines_value):
        prefix = f"lines[{index}]"
        if not isinstance(item, dict):
            raise OperationError("订单明细格式无效。", fields={prefix: "明细须为对象。"})
        if set(item) - {"line_id", "sku_id", "warehouse_id", "quantity", "sale_price"}:
            raise OperationError("订单明细包含不支持的字段。", fields={prefix: "请刷新后重试。"})
        line_id = _optional_identifier(item.get("line_id"), f"{prefix}.line_id")
        if line_id is not None:
            if order is None:
                raise OperationError("新订单不能指定历史明细编号。", fields={f"{prefix}.line_id": "请移除该编号。"})
            if line_id in seen_line_ids:
                raise OperationError("同一订单明细不能重复提交。", fields={f"{prefix}.line_id": "明细编号重复。"})
            seen_line_ids.add(line_id)
        sale_price = amount(item.get("sale_price"), f"{prefix}.sale_price")
        lines.append({
            "line_id": line_id,
            "sku_id": identifier(item.get("sku_id"), f"{prefix}.sku_id"),
            "warehouse_id": identifier(item.get("warehouse_id"), f"{prefix}.warehouse_id"),
            "quantity": quantity(item.get("quantity"), f"{prefix}.quantity", positive=True),
            "sale_price": str(sale_price),
        })
    expected_version = None
    if order is not None:
        expected_version = quantity(data.get("expected_version"), "expected_version", positive=True)
    elif data.get("expected_version") not in (None, ""):
        raise OperationError("新订单不需要版本号。", fields={"expected_version": "请移除此字段。"})
    return {
        "order_id": order.pk if order else None,
        "expected_version": expected_version,
        "customer_id": customer_id,
        "customer_address_id": address_id,
        "channel_id": channel_id,
        "delivery_mode": delivery_mode,
        "address_snapshot": address_snapshot,
        "remarks": _text(field("remarks", order.remarks if order else ""), "remarks"),
        "lines": lines,
    }


def _balance_query(keys):
    condition = Q(pk__in=[])
    for warehouse_id, sku_id in sorted(keys):
        condition |= Q(warehouse_id=warehouse_id, sku_id=sku_id)
    return condition


def _order_references(actor, payload, order, *, lock_balances=False):
    existing_customer_id = order.customer_id if order else None
    existing_channel_id = order.channel_id if order else None
    channel = Channel.objects.filter(pk=payload["channel_id"]).first()
    if channel is None or (not channel.is_active and channel.pk != existing_channel_id):
        raise OperationError("请选择启用中的订单渠道。", status=409, code="inactive_reference", fields={"channel_id": "渠道已停用或不存在。"})

    customer = None
    if payload["customer_id"] is not None:
        customer_id = payload["customer_id"]
        unchanged_customer = customer_id == existing_customer_id
        if not unchanged_customer:
            _require(actor, "view_customer")
            customer = customer_queryset_for(actor).filter(pk=customer_id).first()
            if customer is None or not customer.is_active:
                raise OperationError("客户不存在、已归档或不在可见范围。", status=404, code="not_found", fields={"customer_id": "请选择可用客户。"})
        elif payload["customer_address_id"] is not None:
            _require(actor, "view_customer")
            customer = customer_queryset_for(actor).filter(pk=customer_id).first()
            if customer is None:
                raise OperationError("客户不存在或不在可见范围。", status=404, code="not_found", fields={"customer_id": "请选择可见客户。"})
        else:
            customer = Customer.objects.filter(pk=customer_id).first()
            if customer is None:
                raise OperationError("订单关联客户不存在。", status=409, code="inactive_reference")

    address = None
    resolved_snapshot = dict(payload["address_snapshot"])
    if payload["customer_address_id"] is not None:
        if customer is None or not customer.is_active:
            raise OperationError("归档客户不能用于新订单地址。", status=409, code="inactive_reference")
        address = CustomerAddress.objects.filter(
            pk=payload["customer_address_id"], customer=customer, is_active=True
        ).first()
        if address is None:
            raise OperationError("客户地址不存在或已停用。", status=409, code="inactive_reference", fields={"customer_address_id": "请重新选择地址。"})
        resolved_snapshot = {
            "recipient": address.recipient,
            "contact": address.contact,
            "province": address.province,
            "city": address.city,
            "detail": address.detail,
        }

    snapshot = resolved_snapshot
    present = [bool(snapshot[name]) for name in ("recipient", "contact", "province", "city", "detail")]
    if any(present) and not all(present):
        raise OperationError("收件地址须完整填写。", fields={"address_snapshot": "收件人、联系方式、省份、城市及详细地址都必填。"})
    if payload["delivery_mode"] == SalesOrder.DeliveryMode.SHIPMENT and not all(present):
        raise OperationError("快递订单必须填写完整收件地址。", fields={"address_snapshot": "快递订单须填写完整收件信息。"})
    if all(present) and not valid_region_pair(snapshot["province"], snapshot["city"]):
        raise OperationError("省市不匹配或不在中国大陆列表中。", fields={"address_snapshot.city": "请选择该省份下的城市。"})

    old_lines = list(order.lines.filter(active=True).select_related("sku", "warehouse").order_by("warehouse_id", "sku_id", "id")) if order else []
    old_by_id = {line.pk: line for line in old_lines}
    sku_ids = {line["sku_id"] for line in payload["lines"]} | {line.sku_id for line in old_lines}
    warehouse_ids = {line["warehouse_id"] for line in payload["lines"]} | {line.warehouse_id for line in old_lines}
    skus = {
        item.pk: item
        for item in SKU.objects.filter(pk__in=sku_ids).select_related("product__brand").order_by("id")
    }
    warehouses = {
        item.pk: item
        for item in Warehouse.objects.filter(pk__in=warehouse_ids).order_by("id")
    }
    missing_skus = sku_ids - skus.keys()
    missing_warehouses = warehouse_ids - warehouses.keys()
    if missing_skus or missing_warehouses:
        raise OperationError("SKU或仓库不存在。", status=409, code="inactive_reference")

    keys = {(line["warehouse_id"], line["sku_id"]) for line in payload["lines"]}
    keys |= {(line.warehouse_id, line.sku_id) for line in old_lines}
    balances_query = InventoryBalance.objects.filter(_balance_query(keys)).order_by("warehouse_id", "sku_id", "id")
    if lock_balances:
        balances_query = balances_query.select_for_update()
    balances = {(row.warehouse_id, row.sku_id): row for row in balances_query}

    dependencies = {
        "channel": [channel.pk, channel.name, channel.is_active],
        "customer": [customer.pk, customer.owner_id, customer.name, customer.is_active, customer.version] if customer else None,
        "address": [address.pk, address.is_active, address.recipient, address.contact, address.province, address.city, address.detail] if address else None,
        "skus": [
            [sku.pk, sku.is_active, sku.unit, sku.product.is_active, sku.product.brand.is_active]
            for sku in sorted(skus.values(), key=lambda item: item.pk)
        ],
        "warehouses": [[warehouse.pk, warehouse.name, warehouse.is_active] for warehouse in sorted(warehouses.values(), key=lambda item: item.pk)],
        "balances": [
            [key[0], key[1], balances[key].pk, balances[key].on_hand, balances[key].occupied,
             balances[key].reserved, balances[key].hoarded, balances[key].damaged,
             str(balances[key].cost_amount), balances[key].version, balances[key].quantity_version]
            if key in balances else [key[0], key[1], None]
            for key in sorted(keys)
        ],
        "order": [order.pk, order.version, order.status, [
            [line.pk, line.sku_id, line.warehouse_id, line.quantity, str(line.unit_price), str(line.unit_cost_snapshot), line.active]
            for line in order.lines.order_by("id")
        ]] if order else None,
    }
    return channel, customer, address, resolved_snapshot, old_lines, old_by_id, skus, warehouses, balances, dependencies


def _round_cost(value, field):
    try:
        with localcontext() as ctx:
            ctx.prec = 50
            result = value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    except ArithmeticError:
        raise OperationError("金额超出数据库容量。", fields={field: "金额超出允许范围。"}) from None
    return amount(result, field)


def _plan_order(actor, payload, order, *, lock_balances=False):
    channel, customer, address, snapshot, old_lines, old_by_id, skus, warehouses, balances, dependencies = _order_references(
        actor, payload, order, lock_balances=lock_balances
    )
    old_totals = {}
    for line in old_lines:
        key = (line.warehouse_id, line.sku_id)
        old_totals[key] = old_totals.get(key, 0) + line.quantity
    new_totals = {}
    matched = {}
    recalculate = order is None
    for index, line in enumerate(payload["lines"]):
        key = (line["warehouse_id"], line["sku_id"])
        new_totals[key] = new_totals.get(key, 0) + line["quantity"]
        old = old_by_id.get(line["line_id"]) if line["line_id"] is not None else None
        same_item = bool(old and (old.sku_id, old.warehouse_id) == (line["sku_id"], line["warehouse_id"]))
        if old and not same_item:
            old = None
        if not same_item:
            recalculate = True
        elif line["quantity"] > old.quantity:
            recalculate = True
        matched[index] = old

    for key in sorted(new_totals.keys() | old_totals.keys()):
        balance = balances.get(key)
        available = 0 if balance is None else balance.on_hand - balance.occupied - balance.reserved - balance.hoarded - balance.damaged
        available += old_totals.get(key, 0)
        if new_totals.get(key, 0) > available:
            raise OperationError("可售库存不足，请调整商品数量或仓库。", status=409, code="stock_conflict", fields={"lines": f"仓库{key[0]}、SKU {key[1]}可售数量不足。"})

    planned = []
    for index, line in enumerate(payload["lines"]):
        sku = skus[line["sku_id"]]
        warehouse = warehouses[line["warehouse_id"]]
        old = matched[index]
        if not sku.is_active or not sku.product.is_active or not sku.product.brand.is_active or not warehouse.is_active:
            if old is None or line["quantity"] > old.quantity:
                raise OperationError("新增或增加数量只能使用启用中的SKU和仓库。", status=409, code="inactive_reference", fields={f"lines[{index}]": "SKU或仓库已停用。"})
        balance = balances.get((warehouse.pk, sku.pk))
        if balance is None or balance.on_hand <= 0:
            raise OperationError("该仓库SKU没有可计成本的在库数量。", status=409, code="stock_conflict", fields={f"lines[{index}].warehouse_id": "请选择有库存的仓库。"})
        sale_price = Decimal(line["sale_price"])
        revenue = amount(sale_price * line["quantity"], f"lines[{index}].sale_price")
        if recalculate:
            with localcontext() as context:
                context.prec = 50
                unit_cost = _round_cost(balance.cost_amount / balance.on_hand, f"lines[{index}].unit_cost_snapshot")
            cost_mode = "average_recalculation" if old else "average_snapshot"
        else:
            unit_cost = old.unit_cost_snapshot
            if line["quantity"] < old.quantity:
                cost_mode = "carried_reduction"
            elif sale_price != old.unit_price:
                cost_mode = "price_only"
            else:
                cost_mode = old.cost_mode
        line_cost = amount(unit_cost * line["quantity"], f"lines[{index}].order_cost_amount")
        planned.append({
            **line,
            "existing": old,
            "sku": sku,
            "warehouse": warehouse,
            "unit_price": sale_price,
            "revenue_amount": revenue,
            "unit_cost_snapshot": unit_cost,
            "order_cost_amount": line_cost,
            "outbound_freight": Decimal("0.000"),
            "cost_mode": cost_mode,
        })

    revenue_amount = amount(sum((item["revenue_amount"] for item in planned), Decimal(0)), "revenue_amount")
    cost_amount = amount(sum((item["order_cost_amount"] for item in planned), Decimal(0)), "cost_amount")
    margin_amount = amount(revenue_amount - cost_amount, "margin_amount", nonnegative=False)
    commission_estimate = amount(margin_amount * Decimal("0.10"), "commission_estimate", nonnegative=False)
    old_ids = {line.pk for line in old_lines}
    retained_ids = {item["existing"].pk for item in planned if item["existing"] is not None}
    amount_changed = order is None or old_ids != retained_ids
    if order:
        for item in planned:
            old = item["existing"]
            if old is None or (
                old.sku_id != item["sku"].pk or old.warehouse_id != item["warehouse"].pk
                or old.quantity != item["quantity"] or old.unit_price != item["unit_price"]
                or old.unit_cost_snapshot != item["unit_cost_snapshot"]
            ):
                amount_changed = True
    customer_name = customer.name if customer and (not order or customer.pk != order.customer_id) else order.customer_name_snapshot if order else ""
    if address:
        customer_name = customer.name
    return {
        "channel": channel,
        "customer": customer,
        "customer_name_snapshot": customer_name,
        "snapshot": snapshot,
        "old_lines": old_lines,
        "old_totals": old_totals,
        "new_totals": new_totals,
        "balances": balances,
        "dependencies": dependencies,
        "lines": planned,
        "recalculate": recalculate,
        "amount_changed": amount_changed,
        "revenue_amount": revenue_amount,
        "cost_amount": cost_amount,
        "margin_amount": margin_amount,
        "commission_estimate": commission_estimate,
    }


def _order_preview_result(token, control, plan, payload):
    return {
        "preview_token": token,
        "sequence": control.last_sequence,
        "order_id": payload["order_id"],
        "lines": [
            {
                "line_id": item["line_id"],
                "sku_id": item["sku"].pk,
                "code": item["sku"].code,
                "warehouse_id": item["warehouse"].pk,
                "warehouse": item["warehouse"].name,
                "unit": item["sku"].unit,
                "quantity": item["quantity"],
                "sale_price": f"{item['unit_price']:.3f}",
                "revenue_amount": f"{item['revenue_amount']:.3f}",
                "unit_cost_snapshot": f"{item['unit_cost_snapshot']:.3f}",
                "order_cost_amount": f"{item['order_cost_amount']:.3f}",
                "cost_mode": item["cost_mode"],
            }
            for item in plan["lines"]
        ],
        "recalculate_all_costs": plan["recalculate"],
        "revenue_amount": f"{plan['revenue_amount']:.3f}",
        "cost_amount": f"{plan['cost_amount']:.3f}",
        "margin_amount": f"{plan['margin_amount']:.3f}",
        "commission_estimate": f"{plan['commission_estimate']:.3f}",
        "status": SalesOrder.Status.AWAITING_PAYMENT,
    }


def preview_sales_order(actor, data, order_id=None):
    is_amend = order_id is not None
    _require(actor, "change_salesorder" if is_amend else "add_salesorder")
    if is_amend:
        order_id = identifier(order_id, "order_id")
    with _readonly_snapshot():
        control = BusinessControl.objects.get(pk=1)
        order = None
        if is_amend:
            order = sales_order_queryset_for(actor, changing=True).select_related("customer", "channel").filter(pk=order_id).first()
            if order is None:
                raise OperationError("订单不存在或不在可维护范围。", status=404, code="not_found")
            if order.status != SalesOrder.Status.AWAITING_PAYMENT:
                raise OperationError("只有待付款凭证订单可以修改。", status=409, code="state_conflict")
        payload = _normalize_order_input(data, order)
        if order and payload["expected_version"] != order.version:
            raise OperationError("订单已变化，请刷新后重试。", status=409, code="version_conflict")
        plan = _plan_order(actor, payload, order)
        action = "sales_order_amend" if order else "sales_order_submit"
        input_hash = canonical_hash(payload)
        token = signing.dumps(
            {
                "actor": actor.pk,
                "action": action,
                "order_id": order.pk if order else None,
                "sequence": control.last_sequence,
                "dependencies": canonical_hash(plan["dependencies"]),
                "input_hash": input_hash,
                "payload": payload,
            },
            salt=SALES_ORDER_PREVIEW_SALT,
            compress=True,
        )
        return _order_preview_result(token, control, plan, payload)


def _load_sales_preview(actor, preview_token):
    try:
        data = signing.loads(preview_token, salt=SALES_ORDER_PREVIEW_SALT, max_age=600)
    except signing.SignatureExpired:
        raise OperationError("订单预览已过期，请重新预览。", status=409, code="stale_preview") from None
    except signing.BadSignature:
        raise OperationError("订单预览无效，请重新预览。", status=409, code="stale_preview") from None
    if data.get("actor") != actor.pk or data.get("action") not in ("sales_order_submit", "sales_order_amend"):
        raise OperationError("订单预览无效，请重新预览。", status=409, code="stale_preview")
    return data


def confirm_sales_order(actor, operation_key_value, preview_token, request_id=""):
    preview = _load_sales_preview(actor, preview_token)
    action = preview["action"]
    is_amend = action == "sales_order_amend"
    _require(actor, "change_salesorder" if is_amend else "add_salesorder")
    key = operation_uuid(operation_key_value)
    input_hash = canonical_hash({"action": action, "input_hash": preview["input_hash"]})

    def write():
        with transaction.atomic():
            control = _lock_control()
            _require(actor, "change_salesorder" if is_amend else "add_salesorder")
            replay = _operation_replay(actor, key, action, input_hash)
            if replay is not None:
                return replay
            if control.last_sequence != preview["sequence"]:
                raise OperationError("预览后业务数据已变化，请重新预览。", status=409, code="stale_preview")
            order = None
            if is_amend:
                order = sales_order_queryset_for(actor, changing=True).select_for_update().filter(pk=preview["order_id"]).first()
                if order is None:
                    raise OperationError("订单不存在或不在可维护范围。", status=404, code="not_found")
                set_operation_document_id(order.pk)
                if order.status != SalesOrder.Status.AWAITING_PAYMENT:
                    raise OperationError("只有待付款凭证订单可以修改。", status=409, code="state_conflict")
                if order.version != preview["payload"]["expected_version"]:
                    raise OperationError("订单已变化，请重新预览。", status=409, code="version_conflict")
            payload = _normalize_order_input(preview["payload"], order)
            plan = _plan_order(actor, payload, order, lock_balances=True)
            if canonical_hash(plan["dependencies"]) != preview["dependencies"]:
                raise OperationError("订单预览依赖资料已变化，请重新预览。", status=409, code="stale_preview")

            sequence = _next_sequence(control)
            if order is None:
                order = SalesOrder.objects.create(
                    salesperson=actor,
                    customer=plan["customer"],
                    channel=plan["channel"],
                    status=SalesOrder.Status.AWAITING_PAYMENT,
                    delivery_mode=payload["delivery_mode"],
                    customer_name_snapshot=plan["customer_name_snapshot"],
                    recipient_snapshot=plan["snapshot"]["recipient"],
                    contact_snapshot=plan["snapshot"]["contact"],
                    province_snapshot=plan["snapshot"]["province"],
                    city_snapshot=plan["snapshot"]["city"],
                    detail_snapshot=plan["snapshot"]["detail"],
                    remarks=payload["remarks"],
                    revenue_amount=plan["revenue_amount"],
                    cost_amount=plan["cost_amount"],
                    margin_amount=plan["margin_amount"],
                    commission_estimate=plan["commission_estimate"],
                    last_sequence=sequence,
                )
            old_line_ids = {line.pk for line in plan["old_lines"]}
            retained = set()
            line_records = []
            next_line_number = max(order.lines.values_list("line_number", flat=True), default=0) + 1
            for item in plan["lines"]:
                line = item["existing"]
                if line is None:
                    line = SalesOrderLine.objects.create(
                        order=order,
                        line_number=next_line_number,
                        sku=item["sku"],
                        warehouse=item["warehouse"],
                        quantity=item["quantity"],
                        unit_price=item["unit_price"],
                        revenue_amount=item["revenue_amount"],
                        unit_cost_snapshot=item["unit_cost_snapshot"],
                        order_cost_amount=item["order_cost_amount"],
                        outbound_freight=item["outbound_freight"],
                        cost_mode=item["cost_mode"],
                    )
                    next_line_number += 1
                else:
                    line.sku = item["sku"]
                    line.warehouse = item["warehouse"]
                    line.quantity = item["quantity"]
                    line.unit_price = item["unit_price"]
                    line.revenue_amount = item["revenue_amount"]
                    line.unit_cost_snapshot = item["unit_cost_snapshot"]
                    line.order_cost_amount = item["order_cost_amount"]
                    line.outbound_freight = item["outbound_freight"]
                    line.cost_mode = item["cost_mode"]
                    line.save(update_fields=("sku", "warehouse", "quantity", "unit_price", "revenue_amount", "unit_cost_snapshot", "order_cost_amount", "outbound_freight", "cost_mode"))
                    retained.add(line.pk)
                item["saved_line_id"] = line.pk
                line_records.append(line)
            for line in plan["old_lines"]:
                if line.pk not in retained:
                    line.active = False
                    line.save(update_fields=("active",))

            old_totals = plan["old_totals"]
            new_totals = plan["new_totals"]
            movements = []
            for stock_key in sorted(old_totals.keys() | new_totals.keys()):
                delta = new_totals.get(stock_key, 0) - old_totals.get(stock_key, 0)
                if not delta:
                    continue
                balance = plan["balances"][stock_key]
                _check_balance_result(balance, occupied=balance.occupied + delta)
                balance.occupied += delta
                balance.version += 1
                balance.save(update_fields=("occupied", "version"))
                movements.append((balance, delta))

            if is_amend:
                order.customer = plan["customer"]
                order.channel = plan["channel"]
                order.delivery_mode = payload["delivery_mode"]
                order.customer_name_snapshot = plan["customer_name_snapshot"]
                order.recipient_snapshot = plan["snapshot"]["recipient"]
                order.contact_snapshot = plan["snapshot"]["contact"]
                order.province_snapshot = plan["snapshot"]["province"]
                order.city_snapshot = plan["snapshot"]["city"]
                order.detail_snapshot = plan["snapshot"]["detail"]
                order.remarks = payload["remarks"]
                order.revenue_amount = plan["revenue_amount"]
                order.cost_amount = plan["cost_amount"]
                order.margin_amount = plan["margin_amount"]
                order.commission_estimate = plan["commission_estimate"]
                order.version += 1
                if plan["amount_changed"]:
                    order.amount_version += 1
                order.last_sequence = sequence
                order.save()

            operation_lines = [
                {
                    "line_id": item["saved_line_id"], "sku_id": item["sku"].pk,
                    "warehouse_id": item["warehouse"].pk, "quantity": item["quantity"],
                    "sale_price": f"{item['unit_price']:.3f}",
                    "unit_cost_snapshot": f"{item['unit_cost_snapshot']:.3f}",
                    "order_cost_amount": f"{item['order_cost_amount']:.3f}",
                    "cost_mode": item["cost_mode"],
                }
                for item in plan["lines"]
            ]
            operation = BusinessOperation.objects.create(
                operation_key=key,
                sequence=sequence,
                actor=actor,
                action=action,
                input_hash=input_hash,
                payload={
                    "order_id": order.pk,
                    "expected_version": payload["expected_version"],
                    "customer_id": payload["customer_id"],
                    "channel_id": payload["channel_id"],
                    "delivery_mode": payload["delivery_mode"],
                    "lines": operation_lines,
                    "recalculate_all_costs": plan["recalculate"],
                    "retired_line_ids": sorted(old_line_ids - retained),
                },
                sales_order=order,
            )
            for balance, delta in movements:
                InventoryMovement.objects.create(
                    operation=operation,
                    balance=balance,
                    sales_order=order,
                    event_kind="order_amend" if is_amend else "order_submit",
                    entry_key=f"warehouse:{balance.warehouse_id}:sku:{balance.sku_id}",
                    occupied_delta=delta,
                    cost_revision=balance.version,
                )
            set_operation_document_id(order.pk)
            result = {"document_id": order.pk, "order_id": order.pk, "sequence": sequence, "version": order.version, "status": order.status}
            operation.result = result
            operation.save(update_fields=("result",))
            return result

    return _run_write(action, actor, request_id, write)


def cancel_sales_order(actor, order_id, data, request_id=""):
    _require(actor, "cancel_salesorder")
    order_id = identifier(order_id, "order_id")
    if not isinstance(data, dict) or set(data) - {"expected_version", "operation_key"}:
        raise OperationError("订单取消数据格式无效。")
    expected_version = quantity(data.get("expected_version"), "expected_version", positive=True)
    key = operation_uuid(data.get("operation_key"))
    input_hash = canonical_hash({"action": "sales_order_cancel", "order_id": order_id, "expected_version": expected_version})

    def write():
        with transaction.atomic():
            control = _lock_control()
            _require(actor, "cancel_salesorder")
            replay = _operation_replay(actor, key, "sales_order_cancel", input_hash)
            if replay is not None:
                return replay
            order = sales_order_queryset_for(actor, changing=True).select_for_update().filter(pk=order_id).first()
            if order is None:
                raise OperationError("订单不存在或不在可维护范围。", status=404, code="not_found")
            set_operation_document_id(order.pk)
            if order.status != SalesOrder.Status.AWAITING_PAYMENT:
                raise OperationError("只有未交付订单可以取消。", status=409, code="state_conflict")
            if order.version != expected_version:
                raise OperationError("订单已变化，请刷新后重试。", status=409, code="version_conflict")
            lines = list(order.lines.filter(active=True).order_by("warehouse_id", "sku_id", "id"))
            held = {}
            for line in lines:
                stock_key = (line.warehouse_id, line.sku_id)
                held[stock_key] = held.get(stock_key, 0) + line.quantity
            condition = _balance_query(held)
            balances = {
                (item.warehouse_id, item.sku_id): item
                for item in InventoryBalance.objects.select_for_update().filter(condition).order_by("warehouse_id", "sku_id", "id")
            }
            if balances.keys() != held.keys():
                raise OperationError("订单占用库存记录不完整，请联系管理员。", status=409, code="state_conflict")
            for stock_key, held_quantity in held.items():
                balance = balances[stock_key]
                if balance.occupied < held_quantity:
                    raise OperationError("订单占用数量与库存记录不一致。", status=409, code="state_conflict")
                _check_balance_result(balance, occupied=balance.occupied - held_quantity)
            sequence = _next_sequence(control)
            for stock_key in sorted(held):
                balance = balances[stock_key]
                delta = -held[stock_key]
                balance.occupied += delta
                balance.version += 1
                balance.save(update_fields=("occupied", "version"))
            order.status = SalesOrder.Status.CANCELLED
            order.cancelled_at = timezone.now()
            order.version += 1
            order.last_sequence = sequence
            order.save(update_fields=("status", "cancelled_at", "version", "last_sequence", "updated_at"))
            operation = BusinessOperation.objects.create(
                operation_key=key,
                sequence=sequence,
                actor=actor,
                action="sales_order_cancel",
                input_hash=input_hash,
                payload={"order_id": order.pk, "expected_version": expected_version, "released_holds": [
                    {"warehouse_id": key[0], "sku_id": key[1], "quantity": held[key]} for key in sorted(held)
                ]},
                sales_order=order,
            )
            for stock_key in sorted(held):
                balance = balances[stock_key]
                InventoryMovement.objects.create(
                    operation=operation,
                    balance=balance,
                    sales_order=order,
                    event_kind="order_cancel",
                    entry_key=f"warehouse:{balance.warehouse_id}:sku:{balance.sku_id}",
                    occupied_delta=-held[stock_key],
                    cost_revision=balance.version,
                )
            result = {"document_id": order.pk, "order_id": order.pk, "sequence": sequence, "version": order.version, "status": order.status}
            operation.result = result
            operation.save(update_fields=("result",))
            return result

    return _run_write("sales_order_cancel", actor, request_id, write)
