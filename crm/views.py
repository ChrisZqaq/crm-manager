import json
import re
import uuid

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, JsonResponse
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST, require_safe
from PIL import Image, UnidentifiedImageError
import warnings
from io import BytesIO

from .catalog import CatalogQueryError, paginate, search_products, search_skus, search_warehouses
from .models import (
    AccountProfile,
    Brand,
    BusinessControl,
    Channel,
    Customer,
    CustomerAddress,
    InventoryBalance,
    ReceiptImage,
    SalesOrder,
    SalesOrderLine,
    SKU,
    Supplier,
    Warehouse,
)
from .permissions import CATALOG_VIEW_PERMISSIONS
from .operations import (
    OperationError,
    confirm_receipt,
    create_hold,
    damaged_rows,
    hold_rows,
    inventory_rows,
    release_hold,
    update_global_low_stock,
    update_sku_low_stock,
    preview_receipt,
)
from .opening import confirm_opening, preview_opening
from .permissions import ROLE_OWNER, ROLE_SALES
from .regions import region_options
from .sales import (
    archive_customer,
    cancel_sales_order,
    confirm_sales_order,
    create_customer,
    customer_queryset_for,
    preview_sales_order,
    sales_order_queryset_for,
    transfer_customer,
    update_birthday_preference,
    update_customer,
)


def home(request):
    return redirect("catalog-search")


def _request_id(request):
    requested = request.headers.get("X-Request-ID", "")[:80]
    if requested and re.fullmatch(r"[A-Za-z0-9._-]+", requested):
        return requested
    return str(uuid.uuid4())


def _api_error(request, status, code, message, fields=None):
    return JsonResponse(
        {
            "error": {"code": code, "message": message, "fields": fields or {}, "details": {}},
            "request_id": _request_id(request),
        },
        status=status,
    )


def _api_operation(request, function, *args):
    try:
        return JsonResponse(function(*args, request_id=_request_id(request)))
    except OperationError as exc:
        return _api_error(request, exc.status, exc.code, str(exc), exc.fields)
    except Exception:
        return _api_error(request, 500, "server_error", "操作失败，请联系管理员并提供请求编号。")


def _json_body(request):
    try:
        value = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise OperationError("请求内容须为有效JSON。", fields={"body": "JSON格式无效。"}) from None
    if not isinstance(value, dict):
        raise OperationError("请求内容须为JSON对象。", fields={"body": "请输入对象。"})
    return value


def _api_list_response(request, items):
    try:
        return _api_list(request, items)
    except CatalogQueryError as exc:
        return _api_error(request, 400, "invalid_pagination", str(exc), {"page": str(exc)})


def _operation_error_response(request, callback):
    try:
        return callback()
    except OperationError as exc:
        return _api_error(request, exc.status, exc.code, str(exc), exc.fields)
    except Exception:
        return _api_error(request, 500, "server_error", "操作失败，请联系管理员并提供请求编号。")


def _require_api_access(request, permissions):
    if not request.user.is_authenticated:
        return _api_error(request, 401, "authentication_required", "请先登录。")
    if not request.user.has_perms([f"{app_label}.{codename}" for app_label, codename in permissions]):
        return _api_error(request, 403, "permission_denied", "没有此查询权限。")
    return None


def _api_list(request, items):
    try:
        page = paginate(items, request.GET)
    except CatalogQueryError as exc:
        return _api_error(request, 400, "invalid_pagination", str(exc), {"page": str(exc)})
    page["queried_at"] = timezone.localtime().isoformat(timespec="seconds")
    return JsonResponse(page)


@require_safe
def products_api(request):
    error = _require_api_access(request, CATALOG_VIEW_PERMISSIONS[:2] + (CATALOG_VIEW_PERMISSIONS[2],))
    if error:
        return error
    try:
        return _api_list(request, search_products(request.GET))
    except CatalogQueryError as exc:
        return _api_error(request, 400, "invalid_filter", str(exc), {"filters": str(exc)})


@require_safe
def skus_api(request):
    error = _require_api_access(request, CATALOG_VIEW_PERMISSIONS[:3])
    if error:
        return error
    try:
        return _api_list(request, search_skus(request.GET))
    except CatalogQueryError as exc:
        return _api_error(request, 400, "invalid_filter", str(exc), {"filters": str(exc)})


@require_safe
def warehouses_api(request):
    error = _require_api_access(request, (CATALOG_VIEW_PERMISSIONS[3],))
    if error:
        return error
    return _api_list(request, search_warehouses(request.GET))


@require_safe
def suppliers_api(request):
    error = _require_api_access(request, (("crm", "view_supplier"),))
    if error:
        return error
    items = [
        {"id": str(item.pk), "name": item.name}
        for item in Supplier.objects.filter(is_active=True).order_by("id")
    ]
    return _api_list_response(request, items)


@login_required
@require_safe
def catalog_search(request):
    if not request.user.has_perms([f"{app_label}.{codename}" for app_label, codename in CATALOG_VIEW_PERMISSIONS[:3]]):
        raise PermissionDenied
    try:
        result_page = paginate(search_skus(request.GET), request.GET)
    except CatalogQueryError as exc:
        return render(
            request,
            "crm/catalog.html",
            {"items": [], "page": 1, "total": 0, "brands": Brand.objects.filter(is_active=True), "error": str(exc)},
            status=400,
        )
    return render(
        request,
        "crm/catalog.html",
        {
            **result_page,
            "brands": Brand.objects.filter(is_active=True),
            "error": "",
        },
    )


@require_safe
def inventory_api(request):
    error = _require_api_access(request, (("crm", "view_inventorybalance"),))
    if error:
        return error
    try:
        return _api_list_response(request, inventory_rows(request.user, request.GET))
    except OperationError as exc:
        return _api_error(request, exc.status, exc.code, str(exc), exc.fields)


@require_http_methods(["GET", "POST"])
def holds_api(request):
    error = _require_api_access(request, (("crm", "view_inventoryhold"),))
    if error:
        return error
    if request.method == "GET":
        return _api_list_response(request, hold_rows())
    try:
        data = _json_body(request)
        key = data.pop("operation_key", None)
        return _api_operation(request, create_hold, request.user, data, key)
    except OperationError as exc:
        return _api_error(request, exc.status, exc.code, str(exc), exc.fields)


@require_POST
def hold_release_api(request, hold_id):
    error = _require_api_access(request, (("crm", "release_inventory_hold"),))
    if error:
        return error
    return _operation_error_response(
        request,
        lambda: JsonResponse(release_hold(request.user, hold_id, _json_body(request), _request_id(request))),
    )


@require_safe
def damaged_records_api(request):
    error = _require_api_access(request, (("crm", "view_damagedrecord"),))
    if error:
        return error
    return _api_list_response(request, damaged_rows())


@require_POST
def opening_preview_api(request):
    error = _require_api_access(request, (("crm", "confirm_opening"),))
    if error:
        return error
    uploaded = request.FILES.get("file")
    if not uploaded:
        return _api_error(request, 400, "invalid_input", "请选择期初Excel文件。", {"file": "此项必填。"})
    try:
        mappings = _json_body_value(request.POST.get("mappings", "{}"), "mappings")
        result = preview_opening(request.user, uploaded.read(), uploaded.name, mappings)
        return JsonResponse(result)
    except OperationError as exc:
        return _api_error(request, exc.status, exc.code, str(exc), exc.fields)
    except Exception:
        return _api_error(request, 500, "server_error", "操作失败，请联系管理员并提供请求编号。")


@require_POST
def opening_confirm_api(request):
    error = _require_api_access(request, (("crm", "confirm_opening"),))
    if error:
        return error
    try:
        data = _json_body(request)
        return _api_operation(request, confirm_opening, request.user, data.get("operation_key"), data.get("preview_token"))
    except OperationError as exc:
        return _api_error(request, exc.status, exc.code, str(exc), exc.fields)


@require_POST
def receipt_preview_api(request):
    error = _require_api_access(request, (("crm", "view_inventorybalance"),))
    if error:
        return error
    try:
        return JsonResponse(preview_receipt(request.user, _json_body(request)))
    except OperationError as exc:
        return _api_error(request, exc.status, exc.code, str(exc), exc.fields)
    except Exception:
        return _api_error(request, 500, "server_error", "操作失败，请联系管理员并提供请求编号。")


@require_POST
def receipt_confirm_api(request):
    error = _require_api_access(request, (("crm", "confirm_receipt"),))
    if error:
        return error
    try:
        data = _json_body(request)
        return _api_operation(request, confirm_receipt, request.user, data.get("operation_key"), data.get("preview_token"))
    except OperationError as exc:
        return _api_error(request, exc.status, exc.code, str(exc), exc.fields)


@require_http_methods(["GET", "PATCH"])
def low_stock_settings_api(request):
    if request.method == "PATCH":
        error = _require_api_access(request, (("crm", "manage_global_low_stock"),))
        if error:
            return error
        try:
            data = _json_body(request)
            key = data.pop("operation_key", None)
            return _api_operation(request, update_global_low_stock, request.user, data, key)
        except OperationError as exc:
            return _api_error(request, exc.status, exc.code, str(exc), exc.fields)
    error = _require_api_access(request, (("crm", "view_inventorybalance"),))
    if error:
        return error
    control = BusinessControl.objects.get(pk=1)
    return JsonResponse({"enabled": control.low_stock_enabled, "threshold": control.default_threshold})


@require_http_methods(["PATCH"])
def sku_low_stock_update_api(request, sku_id):
    error = _require_api_access(request, (("crm", "manage_sku_low_stock"),))
    if error:
        return error
    try:
        data = _json_body(request)
        key = data.pop("operation_key", None)
        return _api_operation(request, update_sku_low_stock, request.user, sku_id, data, key)
    except OperationError as exc:
        return _api_error(request, exc.status, exc.code, str(exc), exc.fields)


@require_POST
def receipt_image_upload_api(request):
    error = _require_api_access(request, (("crm", "confirm_receipt"),))
    if error:
        return error
    upload = request.FILES.get("file")
    if not upload:
        return _api_error(request, 400, "invalid_input", "请选择图片文件。", {"file": "此项必填。"})
    if upload.size > 10 * 1024 * 1024:
        return _api_error(request, 400, "invalid_input", "单张图片不能超过10MB。", {"file": "文件过大。"})
    data = upload.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        return _api_error(request, 400, "invalid_input", "单张图片不能超过10MB。", {"file": "文件过大。"})
    signatures = (
        ("JPEG", data.startswith(b"\xff\xd8\xff")),
        ("PNG", data.startswith(b"\x89PNG\r\n\x1a\n")),
        ("WEBP", len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"),
    )
    expected_format = next((image_format for image_format, matched in signatures if matched), None)
    allowed_formats = ["JPEG", "PNG", "WEBP"]
    mime_formats = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}
    if upload.content_type and mime_formats.get(upload.content_type.lower()) != expected_format:
        return _api_error(request, 400, "invalid_input", "图片类型与文件内容不匹配。", {"file": "请上传有效的JPEG、PNG或WebP图片。"})
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data), formats=allowed_formats) as image:
                image_format = image.format
                image.verify()
            with Image.open(BytesIO(data), formats=allowed_formats) as image:
                image.load()
                dimensions_valid = image.width > 0 and image.height > 0
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning, ValueError):
        return _api_error(request, 400, "invalid_input", "图片内容损坏或格式不受支持。", {"file": "请上传有效的JPEG、PNG或WebP图片。"})
    if image_format != expected_format or not dimensions_valid:
        return _api_error(request, 400, "invalid_input", "图片内容与文件格式不匹配。", {"file": "请上传有效的JPEG、PNG或WebP图片。"})
    record = ReceiptImage(owner=request.user, image_format=image_format, size=len(data))
    record.file.save("receipt.image", ContentFile(data), save=True)
    return JsonResponse({"image_id": str(record.pk), "format": image_format.lower(), "size": len(data)})


@require_safe
def receipt_image_api(request, image_id):
    image = get_object_or_404(ReceiptImage, pk=image_id)
    if image.receipt_id is None:
        if image.owner_id != request.user.pk:
            return _api_error(request, 404, "not_found", "图片不存在。")
    else:
        error = _require_api_access(request, (("crm", "view_receipt"),))
        if error:
            return error
    content_type = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}.get(image.image_format, "application/octet-stream")
    return FileResponse(default_storage.open(image.file.name, "rb"), content_type=content_type)


def _json_body_value(value, field):
    try:
        data = json.loads(value or "{}")
    except json.JSONDecodeError:
        raise OperationError("映射字段须为有效JSON。", fields={field: "JSON格式无效。"}) from None
    if not isinstance(data, dict):
        raise OperationError("映射字段须为JSON对象。", fields={field: "请输入对象。"})
    return data


@login_required
@require_safe
def inventory_page(request):
    if not request.user.has_perm("crm.view_inventorybalance") and not request.user.is_superuser:
        raise PermissionDenied
    try:
        items = inventory_rows(request.user, request.GET)
        result_page = paginate(items, request.GET)
        error = ""
    except (CatalogQueryError, OperationError) as exc:
        result_page = {"items": [], "page": 1, "total": 0, "page_size": 50}
        error = str(exc)
    control = BusinessControl.objects.get(pk=1)
    return render(request, "crm/inventory.html", {**result_page, "error": error, "control": control})


@login_required
@require_safe
def receiving_page(request):
    if not request.user.has_perm("crm.confirm_receipt") and not request.user.is_superuser:
        raise PermissionDenied
    return render(
        request,
        "crm/receiving.html",
        {
            "warehouses": Warehouse.objects.filter(is_active=True).order_by("id"),
            "suppliers": Supplier.objects.filter(is_active=True).order_by("id"),
            "skus": SKU.objects.filter(is_active=True, product__is_active=True, product__brand__is_active=True).select_related("product__brand").order_by("id"),
        },
    )


@login_required
@require_safe
def opening_page(request):
    if not request.user.has_perm("crm.confirm_opening") and not request.user.is_superuser:
        raise PermissionDenied
    control = BusinessControl.objects.get(pk=1)
    return render(request, "crm/opening.html", {"opening_completed": control.opening_completed})


@login_required
@require_safe
def holds_page(request):
    if not request.user.has_perm("crm.view_inventoryhold") and not request.user.is_superuser:
        raise PermissionDenied
    balances = InventoryBalance.objects.select_related("warehouse", "sku__product__brand").filter(
        warehouse__is_active=True, sku__is_active=True
    ).order_by("id")
    return render(request, "crm/holds.html", {"holds": hold_rows(), "balances": balances})


def _customer_data(customer, *, birthday_today=False):
    return {
        "id": customer.pk,
        "name": customer.name,
        "contact": customer.contact,
        "company": customer.company,
        "aliases": customer.aliases,
        "birthday": customer.birthday.isoformat() if customer.birthday else None,
        "interests": customer.interests,
        "hobbies": customer.hobbies,
        "owner_id": customer.owner_id,
        "owner_name": customer.owner.get_username(),
        "is_active": customer.is_active,
        "version": customer.version,
        "birthday_today": birthday_today,
        "addresses": [
            {
                "id": address.pk,
                "recipient": address.recipient,
                "contact": address.contact,
                "province": address.province,
                "city": address.city,
                "detail": address.detail,
            }
            for address in customer.addresses.filter(is_active=True).order_by("id")
        ],
    }


def _sales_order_data(order):
    return {
        "id": order.pk,
        "sequence": order.last_sequence,
        "salesperson_id": order.salesperson_id,
        "salesperson": order.salesperson.get_username(),
        "customer_id": order.customer_id,
        "customer_name": order.customer_name_snapshot,
        "channel_id": order.channel_id,
        "channel": order.channel.name,
        "status": order.status,
        "delivery_mode": order.delivery_mode,
        "recipient": order.recipient_snapshot,
        "contact": order.contact_snapshot,
        "province": order.province_snapshot,
        "city": order.city_snapshot,
        "detail": order.detail_snapshot,
        "remarks": order.remarks,
        "revenue_amount": f"{order.revenue_amount:.3f}",
        "cost_amount": f"{order.cost_amount:.3f}",
        "margin_amount": f"{order.margin_amount:.3f}",
        "commission_estimate": f"{order.commission_estimate:.3f}",
        "version": order.version,
        "amount_version": order.amount_version,
        "lines": [
            {
                "line_id": line.pk,
                "sku_id": line.sku_id,
                "sku_code": line.sku.code,
                "warehouse_id": line.warehouse_id,
                "warehouse": line.warehouse.name,
                "quantity": line.quantity,
                "unit_price": f"{line.unit_price:.3f}",
                "revenue_amount": f"{line.revenue_amount:.3f}",
                "unit_cost_snapshot": f"{line.unit_cost_snapshot:.3f}",
                "order_cost_amount": f"{line.order_cost_amount:.3f}",
                "cost_mode": line.cost_mode,
            }
            for line in order.lines.filter(active=True).select_related("sku", "warehouse").order_by("line_number")
        ],
    }


@require_safe
def regions_api(request):
    if not request.user.is_authenticated:
        return _api_error(request, 401, "authentication_required", "请先登录。")
    return JsonResponse({"items": region_options()})


@require_safe
def channels_api(request):
    error = _require_api_access(request, (("crm", "view_channel"),))
    if error:
        return error
    return JsonResponse({"items": list(Channel.objects.filter(is_active=True).order_by("id").values("id", "name"))})


@require_http_methods(["GET", "POST"])
def customers_api(request):
    if request.method == "POST":
        error = _require_api_access(request, (("crm", "add_customer"),))
        if error:
            return error
        try:
            data = _json_body(request)
            operation_key = data.pop("operation_key", None)
            return _api_operation(request, create_customer, request.user, data, operation_key)
        except OperationError as exc:
            return _api_error(request, exc.status, exc.code, str(exc), exc.fields)

    error = _require_api_access(request, (("crm", "view_customer"),))
    if error:
        return error
    include_archived = request.GET.get("include_archived", "false")
    birthday_filter = request.GET.get("birthday_today", "false")
    if include_archived not in ("true", "false") or birthday_filter not in ("true", "false"):
        return _api_error(request, 400, "invalid_filter", "筛选参数须为true或false。")
    profile = AccountProfile.objects.filter(user=request.user).first()
    reminders_enabled = profile.birthday_reminder_enabled if profile else True
    if birthday_filter == "true" and not reminders_enabled:
        return _api_error(request, 409, "preference_disabled", "生日提醒已关闭。")
    items = customer_queryset_for(request.user).select_related("owner")
    if include_archived == "false":
        items = items.filter(is_active=True)
    query = request.GET.get("q", "").strip()
    if query:
        items = items.filter(Q(name__icontains=query) | Q(company__icontains=query) | Q(contact__icontains=query))
    today = timezone.localdate()
    if birthday_filter == "true":
        items = items.filter(birthday__month=today.month, birthday__day=today.day)
    try:
        page = paginate(items.order_by("name", "id"), request.GET)
    except CatalogQueryError as exc:
        return _api_error(request, 400, "invalid_pagination", str(exc), {"page": str(exc)})
    page["items"] = [
        _customer_data(item, birthday_today=bool(item.birthday and (item.birthday.month, item.birthday.day) == (today.month, today.day)))
        for item in page["items"]
    ]
    page["birthday_reminder_enabled"] = reminders_enabled
    page["queried_at"] = timezone.localtime().isoformat(timespec="seconds")
    return JsonResponse(page)


@require_http_methods(["GET", "PATCH"])
def customer_detail_api(request, customer_id):
    permission = "change_customer" if request.method == "PATCH" else "view_customer"
    error = _require_api_access(request, (("crm", permission),))
    if error:
        return error
    customer = customer_queryset_for(request.user, changing=request.method == "PATCH").select_related("owner").filter(pk=customer_id).first()
    if customer is None:
        return _api_error(request, 404, "not_found", "客户不存在或不在可见范围。")
    if request.method == "GET":
        return JsonResponse(_customer_data(customer))
    try:
        data = _json_body(request)
        return _api_operation(request, update_customer, request.user, customer_id, data)
    except OperationError as exc:
        return _api_error(request, exc.status, exc.code, str(exc), exc.fields)


@require_POST
def customer_archive_api(request, customer_id):
    error = _require_api_access(request, (("crm", "archive_customer"),))
    if error:
        return error
    try:
        return _api_operation(request, archive_customer, request.user, customer_id, _json_body(request))
    except OperationError as exc:
        return _api_error(request, exc.status, exc.code, str(exc), exc.fields)


@require_POST
def customer_transfer_api(request, customer_id):
    error = _require_api_access(request, (("crm", "change_customer_owner"),))
    if error:
        return error
    try:
        return _api_operation(request, transfer_customer, request.user, customer_id, _json_body(request))
    except OperationError as exc:
        return _api_error(request, exc.status, exc.code, str(exc), exc.fields)


@require_http_methods(["GET", "PATCH"])
def preferences_api(request):
    error = _require_api_access(request, ())
    if error:
        return error
    if request.method == "GET":
        profile = AccountProfile.objects.filter(user=request.user).first()
        return JsonResponse({
            "birthday_reminder_enabled": profile.birthday_reminder_enabled if profile else True,
            "version": profile.version if profile else 1,
        })
    try:
        result = update_birthday_preference(request.user, _json_body(request))
        return JsonResponse(result)
    except OperationError as exc:
        return _api_error(request, exc.status, exc.code, str(exc), exc.fields)


@require_safe
def birthdays_api(request):
    error = _require_api_access(request, (("crm", "view_customer"),))
    if error:
        return error
    profile = AccountProfile.objects.filter(user=request.user).first()
    enabled = profile.birthday_reminder_enabled if profile else True
    today = timezone.localdate()
    items = []
    if enabled:
        customers = customer_queryset_for(request.user).filter(
            is_active=True, birthday__month=today.month, birthday__day=today.day
        ).select_related("owner").order_by("name", "id")
        items = [_customer_data(item, birthday_today=True) for item in customers]
    return JsonResponse({"date": today.isoformat(), "birthday_reminder_enabled": enabled, "items": items})


@require_http_methods(["GET", "POST"])
def orders_api(request):
    if request.method == "POST":
        error = _require_api_access(request, (("crm", "add_salesorder"),))
        if error:
            return error
        try:
            data = _json_body(request)
            if set(data) - {"operation_key", "preview_token"}:
                raise OperationError("订单确认数据包含不支持的字段。")
            return _api_operation(request, confirm_sales_order, request.user, data.get("operation_key"), data.get("preview_token"))
        except OperationError as exc:
            return _api_error(request, exc.status, exc.code, str(exc), exc.fields)
    error = _require_api_access(request, (("crm", "view_salesorder"),))
    if error:
        return error
    items = sales_order_queryset_for(request.user).select_related("salesperson", "customer", "channel")
    status_value = request.GET.get("status", "")
    if status_value:
        if status_value not in SalesOrder.Status.values:
            return _api_error(request, 400, "invalid_filter", "订单状态无效。", {"status": "请选择支持的订单状态。"})
        items = items.filter(status=status_value)
    query = request.GET.get("q", "").strip()
    if query:
        items = items.filter(Q(customer_name_snapshot__icontains=query) | Q(channel__name__icontains=query) | Q(salesperson__username__icontains=query))
    try:
        page = paginate(items.order_by("-last_sequence", "-id"), request.GET)
    except CatalogQueryError as exc:
        return _api_error(request, 400, "invalid_pagination", str(exc), {"page": str(exc)})
    page["items"] = [_sales_order_data(item) for item in page["items"]]
    page["queried_at"] = timezone.localtime().isoformat(timespec="seconds")
    return JsonResponse(page)


@require_POST
def order_preview_api(request):
    error = _require_api_access(request, (("crm", "add_salesorder"),))
    if error:
        return error
    try:
        return JsonResponse(preview_sales_order(request.user, _json_body(request)))
    except OperationError as exc:
        return _api_error(request, exc.status, exc.code, str(exc), exc.fields)
    except Exception:
        return _api_error(request, 500, "server_error", "操作失败，请联系管理员并提供请求编号。")


@require_safe
def order_detail_api(request, order_id):
    error = _require_api_access(request, (("crm", "view_salesorder"),))
    if error:
        return error
    order = sales_order_queryset_for(request.user).select_related("salesperson", "customer", "channel").filter(pk=order_id).first()
    if order is None:
        return _api_error(request, 404, "not_found", "订单不存在或不在可见范围。")
    return JsonResponse(_sales_order_data(order))


@require_POST
def order_amend_preview_api(request, order_id):
    error = _require_api_access(request, (("crm", "change_salesorder"),))
    if error:
        return error
    try:
        return JsonResponse(preview_sales_order(request.user, _json_body(request), order_id=order_id))
    except OperationError as exc:
        return _api_error(request, exc.status, exc.code, str(exc), exc.fields)
    except Exception:
        return _api_error(request, 500, "server_error", "操作失败，请联系管理员并提供请求编号。")


@require_POST
def order_amend_api(request, order_id):
    error = _require_api_access(request, (("crm", "change_salesorder"),))
    if error:
        return error
    try:
        data = _json_body(request)
        if set(data) - {"operation_key", "preview_token"}:
            raise OperationError("订单确认数据包含不支持的字段。")
        return _api_operation(request, confirm_sales_order, request.user, data.get("operation_key"), data.get("preview_token"))
    except OperationError as exc:
        return _api_error(request, exc.status, exc.code, str(exc), exc.fields)


@require_POST
def order_cancel_api(request, order_id):
    error = _require_api_access(request, (("crm", "cancel_salesorder"),))
    if error:
        return error
    try:
        return _api_operation(request, cancel_sales_order, request.user, order_id, _json_body(request))
    except OperationError as exc:
        return _api_error(request, exc.status, exc.code, str(exc), exc.fields)


def _require_page_permission(request, codename):
    if not request.user.is_superuser and not request.user.has_perm(f"crm.{codename}"):
        raise PermissionDenied


@login_required
@require_safe
def customers_page(request):
    _require_page_permission(request, "view_customer")
    return render(request, "crm/customers.html")


@login_required
@require_safe
def customer_form_page(request, customer_id=None):
    creating = customer_id is None
    _require_page_permission(request, "add_customer" if creating else "change_customer")
    customer = None
    if not creating:
        customer = customer_queryset_for(request.user, changing=True).select_related("owner").filter(pk=customer_id).first()
        if customer is None:
            raise PermissionDenied
    User = get_user_model()
    can_select_owner = not request.user.groups.filter(name=ROLE_SALES).exists() or request.user.has_perm("crm.change_customer_owner") or request.user.is_superuser
    salespeople = User.objects.filter(is_active=True, groups__name=ROLE_SALES).order_by("username") if can_select_owner else []
    initial = _customer_data(customer) if customer else None
    return render(request, "crm/customer_form.html", {
        "customer": customer,
        "initial": initial,
        "salespeople": salespeople,
        "can_select_owner": can_select_owner,
        "can_transfer": bool(customer and (request.user.is_superuser or request.user.has_perm("crm.change_customer_owner"))),
    })


@login_required
@require_safe
def birthdays_page(request):
    _require_page_permission(request, "view_customer")
    profile = AccountProfile.objects.filter(user=request.user).first()
    enabled = profile.birthday_reminder_enabled if profile else True
    today = timezone.localdate()
    customers = []
    if enabled:
        customers = [
            _customer_data(item, birthday_today=True)
            for item in customer_queryset_for(request.user).filter(
                is_active=True, birthday__month=today.month, birthday__day=today.day
            ).select_related("owner").order_by("name", "id")
        ]
    return render(request, "crm/birthdays.html", {"customers": customers, "enabled": enabled, "today": today})


@login_required
@require_safe
def orders_page(request):
    _require_page_permission(request, "view_salesorder")
    return render(request, "crm/orders.html")


@login_required
@require_safe
def order_form_page(request, order_id=None):
    creating = order_id is None
    _require_page_permission(request, "add_salesorder" if creating else "change_salesorder")
    order = None
    initial = None
    if not creating:
        order = sales_order_queryset_for(request.user, changing=True).select_related("salesperson", "customer", "channel").filter(pk=order_id).first()
        if order is None:
            raise PermissionDenied
        initial = _sales_order_data(order)
    return render(request, "crm/order_form.html", {"order": order, "initial": initial})


@login_required
@require_safe
def order_detail_page(request, order_id):
    _require_page_permission(request, "view_salesorder")
    order = sales_order_queryset_for(request.user).select_related("salesperson", "customer", "channel").filter(pk=order_id).first()
    if order is None:
        raise PermissionDenied
    return render(request, "crm/order_detail.html", {"order": order, "initial": _sales_order_data(order)})
