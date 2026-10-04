import json
import re
import uuid

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST, require_safe
from PIL import Image, UnidentifiedImageError
import warnings
from io import BytesIO

from .catalog import CatalogQueryError, paginate, search_products, search_skus, search_warehouses
from .models import Brand
from .permissions import CATALOG_VIEW_PERMISSIONS
from .models import BusinessControl, InventoryBalance, ReceiptImage, SKU, Supplier, Warehouse
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
