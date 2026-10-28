import json
import re
import secrets
import time
import uuid
from urllib.parse import urlsplit

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_http_methods, require_POST, require_safe
from PIL import Image, UnidentifiedImageError
import warnings
from io import BytesIO

from .catalog import CatalogQueryError, paginate, search_products, search_skus, search_warehouses
from .models import (
    AccountProfile,
    Brand,
    BusinessControl,
    BusinessOperation,
    Channel,
    Customer,
    CustomerAddress,
    CommissionReview,
    InventoryBalance,
    CommissionAdjustment,
    PaymentAccount,
    PaymentRecord,
    ReceiptImage,
    OpeningBatch,
    ProductLanguageOption,
    Receipt,
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
    identifier,
    inventory_rows,
    opening_control_data,
    create_catalog_product,
    create_catalog_sku,
    release_hold,
    update_global_low_stock,
    update_opening_control,
    update_sku_low_stock,
    sku_number_suggestion,
    preview_receipt,
)
from .opening import confirm_opening, preview_opening
from .inventory_adjustments import (
    approve_stock_count,
    create_inventory_gift,
    create_inventory_split,
    create_stock_count,
    resolve_damaged_record,
    revise_stock_count,
    stock_count_rows,
)
from .corrections import (
    approve_cost_correction,
    cost_correction_rows,
    preview_cost_correction,
    reject_cost_correction,
    submit_cost_correction,
    visible_cost_correction_data,
)
from .fulfillment import (
    _can_verify,
    _current_cost_correction_commission_suggestion,
    confirm_cost_correction_commission_decision,
    complete_commission_adjustment,
    deliver_on_site,
    receive_sales_order,
    register_payment,
    review_commission,
    ship_sales_order,
    update_outbound_freight,
    verify_payment,
)
from .permissions import ROLE_OWNER, ROLE_SALES, ROLE_WAREHOUSE
from .regions import region_options
from .sales import (
    archive_customer,
    cancel_sales_order,
    confirm_sales_order,
    create_customer,
    customer_owner_queryset,
    customer_queryset_for,
    preview_sales_order,
    sales_order_actor_can_change,
    sales_order_actor_can_fulfill,
    sales_order_commission_visible_to,
    sales_order_queryset_for,
    transfer_customer,
    update_customer,
)
from .reports import REPORTS, _date_criteria, build_report, page_report_data, workbook_bytes
from .wecom import WeComError, authorization_url, authorized_userid


@login_required
def home(request):
    return render(request, "crm/workbench.html")


WECOM_STATE_SESSION_KEY = "wecom_oauth_state"
WECOM_STATE_TTL_SECONDS = 300


def _wecom_response(response):
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response


def _wecom_error(request, message, status, logout_required=False):
    return _wecom_response(render(
        request,
        "registration/wecom_error.html",
        {"message": message, "logout_required": logout_required},
        status=status,
    ))


def _safe_wecom_next(value):
    if isinstance(value, str) and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        try:
            parts = urlsplit(value)
        except ValueError:
            parts = None
        if (
            parts is not None
            and not parts.scheme
            and not parts.netloc
            and not parts.fragment
            and url_has_allowed_host_and_scheme(value, settings.ALLOWED_HOSTS, require_https=True)
        ):
            return value
    return reverse("home")


@require_GET
def wecom_login_start(request):
    request.get_host()
    state = secrets.token_urlsafe(32)
    try:
        destination = authorization_url(state)
    except WeComError:
        return _wecom_error(request, "企业微信登录暂不可用，请联系管理员。", status=503)
    request.session[WECOM_STATE_SESSION_KEY] = {
        "state": state,
        "expires_at": time.time() + WECOM_STATE_TTL_SECONDS,
        "next": _safe_wecom_next(request.GET.get("next", "")),
    }
    return _wecom_response(redirect(destination))


@require_GET
def wecom_login_callback(request):
    request.get_host()
    states = request.GET.getlist("state")
    codes = request.GET.getlist("code")
    pending = request.session.get(WECOM_STATE_SESSION_KEY)
    if (
        len(states) != 1
        or not isinstance(pending, dict)
        or not isinstance(pending.get("state"), str)
        or not pending["state"].isascii()
        or not isinstance(pending.get("expires_at"), (int, float))
    ):
        return _wecom_error(request, "企业微信登录请求已失效，请重新打开入口。", status=400)
    if not states[0] or len(states[0]) > 128 or not states[0].isascii():
        return _wecom_error(request, "企业微信登录请求校验失败，请重新打开入口。", status=400)
    if not secrets.compare_digest(pending["state"], states[0]):
        return _wecom_error(request, "企业微信登录请求校验失败，请重新打开入口。", status=400)
    if pending["expires_at"] < time.time():
        request.session.pop(WECOM_STATE_SESSION_KEY, None)
        return _wecom_error(request, "企业微信登录请求已过期，请重新打开入口。", status=400)

    request.session.pop(WECOM_STATE_SESSION_KEY, None)
    next_url = _safe_wecom_next(pending.get("next"))
    if len(codes) != 1 or not codes[0] or len(codes[0].encode("utf-8")) > 512:
        return _wecom_error(request, "企业微信授权未完成，请重新打开入口。", status=400)
    try:
        userid = authorized_userid(codes[0])
    except WeComError:
        return _wecom_error(request, "企业微信认证服务暂不可用，请稍后重试。", status=502)
    if not userid:
        return _wecom_error(request, "此企业微信账号未绑定已启用的CRM员工账号，请联系管理员。", status=403)
    try:
        profile = AccountProfile.objects.select_related("user").get(wecom_userid__iexact=userid)
    except (AccountProfile.DoesNotExist, AccountProfile.MultipleObjectsReturned):
        return _wecom_error(request, "此企业微信账号未绑定已启用的CRM员工账号，请联系管理员。", status=403)

    user = profile.user
    if not user.is_active:
        return _wecom_error(request, "此企业微信账号未绑定已启用的CRM员工账号，请联系管理员。", status=403)
    if request.user.is_authenticated:
        if request.user.pk != user.pk:
            return _wecom_error(
                request,
                "当前CRM账号与企业微信账号不一致。请先退出当前CRM账号，再从企业微信工作台重新打开。",
                status=409,
                logout_required=True,
            )
        return _wecom_response(redirect(next_url))

    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return _wecom_response(redirect(next_url))


def _request_id(request):
    requested = request.headers.get("X-Request-ID", "")[:80]
    if requested and re.fullmatch(r"[A-Za-z0-9._-]+", requested):
        return requested
    return str(uuid.uuid4())


def _api_error(request, status, code, message, fields=None, details=None):
    return JsonResponse(
        {
            "error": {"code": code, "message": message, "fields": fields or {}, "details": details or {}},
            "request_id": _request_id(request),
        },
        status=status,
    )


def _api_operation(request, function, *args):
    try:
        return JsonResponse(function(*args, request_id=_request_id(request)))
    except OperationError as exc:
        return _api_error(request, exc.status, exc.code, str(exc), exc.fields, exc.details)
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
        return _api_error(request, exc.status, exc.code, str(exc), exc.fields, exc.details)
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


@require_http_methods(["GET", "POST"])
def products_api(request):
    if request.method == "POST":
        error = _require_api_access(request, (("crm", "add_product"), ("crm", "add_sku")))
        if error:
            return error
        try:
            data = _json_body(request)
            operation_key = data.pop("operation_key", None)
            return _api_operation(request, create_catalog_product, request.user, data, operation_key)
        except OperationError as exc:
            return _api_error(request, exc.status, exc.code, str(exc), exc.fields, exc.details)
    error = _require_api_access(request, CATALOG_VIEW_PERMISSIONS[:3])
    if error:
        return error
    try:
        include_inventory = request.GET.get("include_inventory", "false")
        if include_inventory not in ("true", "false"):
            return _api_error(request, 400, "invalid_filter", "include_inventory须为true或false。", {"include_inventory": "请输入true或false。"})
        if include_inventory == "true":
            error = _require_api_access(request, (("crm", "view_inventorybalance"),))
            if error:
                return error
            result = paginate(search_products(request.GET), request.GET)
            sku_ids = {sku["id"] for product in result["items"] for sku in product["skus"]}
            balances = InventoryBalance.objects.filter(sku_id__in=sku_ids).select_related("warehouse", "sku").order_by("id")
            by_sku = {}
            for balance in balances:
                by_sku.setdefault(balance.sku_id, []).append({
                    "warehouse_id": str(balance.warehouse_id),
                    "warehouse": balance.warehouse.name,
                    "available": balance.on_hand - balance.occupied - balance.reserved - balance.hoarded - balance.damaged,
                    "unit": balance.sku.unit,
                })
            for product in result["items"]:
                for sku in product["skus"]:
                    sku["inventory"] = by_sku.get(sku["id"], [])
            result["queried_at"] = timezone.localtime().isoformat(timespec="seconds")
            return JsonResponse(result)
        return _api_list(request, search_products(request.GET))
    except CatalogQueryError as exc:
        return _api_error(request, 400, "invalid_filter", str(exc), {"filters": str(exc)})


@require_http_methods(["GET", "POST"])
def skus_api(request):
    if request.method == "POST":
        error = _require_api_access(request, (("crm", "add_sku"),))
        if error:
            return error
        try:
            data = _json_body(request)
            operation_key = data.pop("operation_key", None)
            return _api_operation(request, create_catalog_sku, request.user, data, operation_key)
        except OperationError as exc:
            return _api_error(request, exc.status, exc.code, str(exc), exc.fields, exc.details)
    error = _require_api_access(request, CATALOG_VIEW_PERMISSIONS[:3])
    if error:
        return error
    try:
        return _api_list(request, search_skus(request.GET))
    except CatalogQueryError as exc:
        return _api_error(request, 400, "invalid_filter", str(exc), {"filters": str(exc)})


@require_safe
def product_languages_api(request):
    error = _require_api_access(request, CATALOG_VIEW_PERMISSIONS[:3])
    if error:
        return error
    return JsonResponse({"items": list(ProductLanguageOption.objects.order_by("id").values("id", "name"))})


@require_safe
def sku_number_suggestion_api(request):
    error = _require_api_access(request, (("crm", "add_product"),))
    if error:
        return error
    try:
        return JsonResponse(sku_number_suggestion(request.user, request.GET.get("brand_id")))
    except OperationError as exc:
        return _api_error(request, exc.status, exc.code, str(exc), exc.fields, exc.details)


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


@login_required
def catalog_product_new(request):
    _require_page_permission(request, "add_product")
    _require_page_permission(request, "add_sku")
    return render(request, "crm/catalog_product_new.html", {
        "brands": Brand.objects.filter(is_active=True).order_by("name"),
        "languages": ProductLanguageOption.objects.order_by("id"),
    })


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
def inventory_split_api(request):
    error = _require_api_access(request, (("crm", "create_inventory_split"),))
    if error:
        return error
    try:
        data = _json_body(request)
        operation_key = data.pop("operation_key", None)
        return _api_operation(request, create_inventory_split, request.user, data, operation_key)
    except OperationError as exc:
        return _api_error(request, exc.status, exc.code, str(exc), exc.fields)


@require_POST
def inventory_gift_api(request):
    error = _require_api_access(request, (("crm", "create_inventory_gift"),))
    if error:
        return error
    try:
        data = _json_body(request)
        operation_key = data.pop("operation_key", None)
        return _api_operation(request, create_inventory_gift, request.user, data, operation_key)
    except OperationError as exc:
        return _api_error(request, exc.status, exc.code, str(exc), exc.fields)


@require_POST
def damage_resolution_api(request, damaged_record_id):
    error = _require_api_access(request, (("crm", "resolve_damaged_record"),))
    if error:
        return error
    try:
        return _api_operation(request, resolve_damaged_record, request.user, damaged_record_id, _json_body(request))
    except OperationError as exc:
        return _api_error(request, exc.status, exc.code, str(exc), exc.fields)


@require_http_methods(["GET", "POST"])
def stock_counts_api(request):
    if request.method == "GET":
        error = _require_api_access(request, (("crm", "view_stockcount"),))
        if error:
            return error
        try:
            return _api_list_response(request, stock_count_rows(request.user, status=request.GET.get("status")))
        except OperationError as exc:
            return _api_error(request, exc.status, exc.code, str(exc), exc.fields)
    error = _require_api_access(request, (("crm", "add_stockcount"),))
    if error:
        return error
    try:
        data = _json_body(request)
        operation_key = data.pop("operation_key", None)
        return _api_operation(request, create_stock_count, request.user, data, operation_key)
    except OperationError as exc:
        return _api_error(request, exc.status, exc.code, str(exc), exc.fields)


@require_safe
def stock_count_detail_api(request, count_id):
    error = _require_api_access(request, (("crm", "view_stockcount"),))
    if error:
        return error
    try:
        rows = stock_count_rows(request.user, count_id=count_id)
    except OperationError as exc:
        return _api_error(request, exc.status, exc.code, str(exc), exc.fields)
    if not rows:
        return _api_error(request, 404, "not_found", "盘点记录不存在。")
    return JsonResponse(rows[0])


@require_http_methods(["PATCH"])
def stock_count_update_api(request, count_id):
    error = _require_api_access(request, (("crm", "add_stockcount"),))
    if error:
        return error
    try:
        return _api_operation(request, revise_stock_count, request.user, count_id, _json_body(request))
    except OperationError as exc:
        return _api_error(request, exc.status, exc.code, str(exc), exc.fields)


@require_POST
def stock_count_approve_api(request, count_id):
    error = _require_api_access(request, (("crm", "approve_stockcount"),))
    if error:
        return error
    try:
        return _api_operation(request, approve_stock_count, request.user, count_id, _json_body(request))
    except OperationError as exc:
        return _api_error(request, exc.status, exc.code, str(exc), exc.fields)


@require_http_methods(["GET", "POST"])
def cost_corrections_api(request):
    if request.method == "GET":
        error = _require_api_access(request, (("crm", "view_costcorrection"),))
        if error:
            return error
        try:
            return _api_list_response(request, cost_correction_rows(request.user, status=request.GET.get("status")))
        except OperationError as exc:
            return _api_error(request, exc.status, exc.code, str(exc), exc.fields)
    error = _require_api_access(request, (("crm", "submit_costcorrection"),))
    if error:
        return error
    try:
        data = _json_body(request)
        operation_key = data.pop("operation_key", None)
        preview_token = data.pop("preview_token", None)
        return _api_operation(request, submit_cost_correction, request.user, operation_key, preview_token)
    except OperationError as exc:
        return _api_error(request, exc.status, exc.code, str(exc), visible_cost_correction_data(request.user, exc.fields))


@require_POST
def cost_correction_preview_api(request):
    error = _require_api_access(request, (("crm", "submit_costcorrection"),))
    if error:
        return error
    try:
        return JsonResponse(preview_cost_correction(request.user, _json_body(request)))
    except OperationError as exc:
        return _api_error(request, exc.status, exc.code, str(exc), visible_cost_correction_data(request.user, exc.fields))
    except Exception:
        return _api_error(request, 500, "server_error", "操作失败，请联系管理员并提供请求编号。")


@require_safe
def cost_correction_detail_api(request, correction_id):
    error = _require_api_access(request, (("crm", "view_costcorrection"),))
    if error:
        return error
    try:
        rows = cost_correction_rows(request.user, correction_id=correction_id)
    except OperationError as exc:
        return _api_error(request, exc.status, exc.code, str(exc), exc.fields)
    if not rows:
        return _api_error(request, 404, "not_found", "纠错记录不存在。")
    return JsonResponse(rows[0])


@require_POST
def cost_correction_approve_api(request, correction_id):
    error = _require_api_access(request, (("crm", "approve_costcorrection"),))
    if error:
        return error
    try:
        return _api_operation(request, approve_cost_correction, request.user, correction_id, _json_body(request))
    except OperationError as exc:
        return _api_error(request, exc.status, exc.code, str(exc), visible_cost_correction_data(request.user, exc.fields))


@require_POST
def cost_correction_reject_api(request, correction_id):
    error = _require_api_access(request, (("crm", "reject_costcorrection"),))
    if error:
        return error
    try:
        return _api_operation(request, reject_cost_correction, request.user, correction_id, _json_body(request))
    except OperationError as exc:
        return _api_error(request, exc.status, exc.code, str(exc), visible_cost_correction_data(request.user, exc.fields))


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


@require_http_methods(["GET", "PATCH"])
def opening_control_api(request):
    error = _require_api_access(request, ())
    if error:
        return error
    if request.method == "GET":
        try:
            return JsonResponse(opening_control_data(request.user))
        except OperationError as exc:
            return _api_error(request, exc.status, exc.code, str(exc), exc.fields)
    try:
        return _api_operation(request, update_opening_control, request.user, _json_body(request))
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
@require_POST
def receipt_image_upload_api(request):
    if not request.user.is_authenticated:
        return _api_error(request, 401, "authentication_required", "请先登录。")
    if not (request.user.has_perm("crm.confirm_receipt") or request.user.has_perm("crm.register_payment")):
        return _api_error(request, 403, "permission_denied", "没有上传单据图片的权限。")
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
    if not request.user.is_authenticated:
        return _api_error(request, 401, "authentication_required", "请先登录。")
    image = get_object_or_404(ReceiptImage, pk=image_id)
    if image.receipt_id is None and image.payment_id is None:
        if image.owner_id != request.user.pk:
            return _api_error(request, 404, "not_found", "图片不存在。")
    elif image.receipt_id is not None:
        error = _require_api_access(request, (("crm", "view_receipt"),))
        if error:
            return error
    else:
        error = _require_api_access(request, (("crm", "view_salesorder"),))
        if error:
            return error
        if not sales_order_queryset_for(request.user).filter(pk=image.payment.order_id).exists():
            return _api_error(request, 404, "not_found", "图片不存在。")
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


def _report_permission(request, report_name):
    config = REPORTS.get(report_name)
    if config is None:
        raise Http404("报表不存在。")
    _require_page_permission(request, config["permission"])


def _report_download_response(report_name, data):
    response = HttpResponse(
        workbook_bytes(data),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{report_name}_{data["metadata"]["queried_at"][:10]}.xlsx"'
    response["X-Report-Start-Date"] = data["metadata"].get("start_date") or ""
    response["X-Report-End-Date"] = data["metadata"].get("end_date") or ""
    response["X-Report-Queried-At"] = data["metadata"]["queried_at"]
    return response


@login_required
@require_safe
def report_page(request, report_name):
    _report_permission(request, report_name)
    error = ""
    data = None
    result = {}
    try:
        data = build_report(request.user, report_name, request.GET)
        try:
            result = page_report_data(request.GET, data, request.GET.get("page", 1))
        except CatalogQueryError as exc:
            error = str(exc)
            fallback_query = request.GET.copy()
            fallback_query.pop("page", None)
            fallback_query.pop("page_size", None)
            result = page_report_data(fallback_query, data, 1)
    except OperationError as exc:
        error = str(exc)
    context = {
        **result,
        "report_name": report_name,
        "report_title": REPORTS[report_name]["title"],
        "export_url": f"{reverse('report-export', args=(report_name,))}?{request.GET.urlencode()}",
        "error": error,
        "prev_url": "",
        "next_url": "",
    }
    if data is not None and result:
        query = request.GET.copy()
        if result["has_previous"]:
            query["page"] = result["page"] - 1
            context["prev_url"] = f"?{query.urlencode()}"
        if result["has_next"]:
            query["page"] = result["page"] + 1
            context["next_url"] = f"?{query.urlencode()}"
    return render(request, "crm/report.html", context)


@login_required
@require_safe
def report_page_export(request, report_name):
    _report_permission(request, report_name)
    try:
        return _report_download_response(report_name, build_report(request.user, report_name, request.GET))
    except OperationError as exc:
        return HttpResponse(str(exc), status=exc.status, content_type="text/plain; charset=utf-8")


@require_safe
def reports_api(request, report_name):
    if report_name not in REPORTS:
        return _api_error(request, 404, "not_found", "报表不存在。")
    error = _require_api_access(request, (("crm", REPORTS[report_name]["permission"]),))
    if error:
        return error
    try:
        data = build_report(request.user, report_name, request.GET)
        result = page_report_data(request.GET, data, request.GET.get("page", 1))
        return JsonResponse({
            "report": report_name,
            "timezone": data["metadata"]["timezone"],
            "period": data["metadata"].get("period"),
            "start_date": data["metadata"].get("start_date"),
            "end_date": data["metadata"].get("end_date"),
            "period_label": data["metadata"]["period_label"],
            "date_basis": data["metadata"]["date_basis"],
            "date_filter_available": data["metadata"]["date_filter_available"],
            "payment_date_basis_pending": data["metadata"]["payment_date_basis_pending"],
            "queried_at": data["metadata"]["queried_at"],
            "filter_summary": data["metadata"]["filter_summary"],
            "summary": data["summary"],
            **({"filter_options": data["filter_options"]} if report_name == "inventory" else {}),
            **({"series": result["series"]} if "series" in result else {}),
            "columns": [{"key": key, "label": label, "type": kind} for key, label, kind in data["columns"]],
            **{key: result[key] for key in ("items", "total", "page", "page_size", "has_previous", "has_next")},
        })
    except (OperationError, CatalogQueryError) as exc:
        if isinstance(exc, OperationError):
            return _api_error(request, exc.status, exc.code, str(exc), exc.fields)
        return _api_error(request, 400, "invalid_pagination", str(exc), {"page": str(exc)})


@require_safe
def reports_export_api(request, report_name):
    if report_name not in REPORTS:
        return _api_error(request, 404, "not_found", "报表不存在。")
    error = _require_api_access(request, (("crm", REPORTS[report_name]["permission"]),))
    if error:
        return error
    try:
        return _report_download_response(report_name, build_report(request.user, report_name, request.GET))
    except OperationError as exc:
        return _api_error(request, exc.status, exc.code, str(exc), exc.fields)
    except CatalogQueryError as exc:
        return _api_error(request, 400, "invalid_filter", str(exc))


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


def adjustments_page(request):
    operation_permissions = (
        "create_inventory_split", "create_inventory_gift", "resolve_damaged_record",
        "add_stockcount", "submit_costcorrection", "approve_stockcount",
        "approve_costcorrection", "view_stockcount", "view_costcorrection",
    )
    if not request.user.is_superuser and not any(request.user.has_perm(f"crm.{item}") for item in operation_permissions):
        raise PermissionDenied
    can_submit_correction = request.user.is_superuser or request.user.has_perm("crm.submit_costcorrection")
    correction_sources = {"receipts": [], "openings": []}
    if can_submit_correction:
        for receipt in Receipt.objects.select_related("supplier", "warehouse").prefetch_related("lines__sku__product"):
            correction_sources["receipts"].append({
                "id": receipt.pk,
                "label": f"采购单 {receipt.sequence} · {receipt.supplier.name} · {receipt.warehouse.name}",
                "input": {
                    "warehouse_id": receipt.warehouse_id, "warehouse_label": receipt.warehouse.name,
                    "lines": [{
                        "line_number": line.line_number, "sku_id": line.sku_id,
                        "sku_label": f"{line.sku.code} · {line.sku.product.name} · {line.sku.unit}",
                        "quantity": line.quantity, "purchase_price": f"{line.purchase_price:.3f}",
                        "inbound_freight": f"{line.inbound_freight:.3f}",
                        "reserved_quantity": line.reserved_quantity, "damaged_quantity": line.damaged_quantity,
                    } for line in receipt.lines.all()],
                },
            })
        for batch in OpeningBatch.objects.prefetch_related("lines__sku__product", "lines__warehouse"):
            correction_sources["openings"].append({
                "id": batch.pk,
                "label": f"期初批次 {batch.sequence}",
                "input": {"lines": [{
                    "line_number": line.line_number, "warehouse_id": line.warehouse_id,
                    "warehouse_label": line.warehouse.name, "sku_id": line.sku_id,
                    "sku_label": f"{line.sku.code} · {line.sku.product.name} · {line.sku.unit}",
                    "quantity": line.quantity, "unit_cost": f"{line.unit_cost:.3f}",
                    "reserved_quantity": line.reserved_quantity, "hoarded_quantity": line.hoarded_quantity,
                    "damaged_quantity": line.damaged_quantity,
                } for line in batch.lines.all()]},
            })
    return render(request, "crm/adjustments.html", {
        "warehouses": Warehouse.objects.filter(is_active=True).order_by("id"),
        "skus": SKU.objects.filter(is_active=True, product__is_active=True, product__brand__is_active=True)
        .select_related("product__brand").order_by("id"),
        "can_split": request.user.is_superuser or request.user.has_perm("crm.create_inventory_split"),
        "can_gift": request.user.is_superuser or request.user.has_perm("crm.create_inventory_gift"),
        "can_resolve_damage": request.user.is_superuser or request.user.has_perm("crm.resolve_damaged_record"),
        "can_add_count": request.user.is_superuser or request.user.has_perm("crm.add_stockcount"),
        "can_approve_count": request.user.is_superuser or request.user.has_perm("crm.approve_stockcount"),
        "can_submit_correction": can_submit_correction,
        "can_approve_correction": request.user.is_superuser or request.user.has_perm("crm.approve_costcorrection"),
        "can_reject_correction": request.user.is_superuser or request.user.has_perm("crm.reject_costcorrection"),
        "can_view_count": request.user.is_superuser or request.user.has_perm("crm.view_stockcount"),
        "can_view_correction": request.user.is_superuser or request.user.has_perm("crm.view_costcorrection"),
        "can_approve_own_count": request.user.groups.filter(name=ROLE_OWNER).exists(),
        "current_user_id": request.user.pk,
        "correction_sources": correction_sources,
    })


def _customer_data(customer):
    return {
        "id": customer.pk,
        "name": customer.name,
        "contact": customer.contact,
        "company": customer.company,
        "aliases": customer.aliases,
        "note1": customer.note1,
        "note2": customer.note2,
        "invoice_title": customer.invoice_title,
        "invoice_credit_code": customer.invoice_credit_code,
        "invoice_email": customer.invoice_email,
        "owner_id": customer.owner_id,
        "owner_name": customer.owner.get_username(),
        "is_active": customer.is_active,
        "version": customer.version,
        "addresses": [
            {
                "id": address.pk,
                "recipient": address.recipient,
                "phone": address.contact,
                "province": address.province,
                "city": address.city,
                "detail": address.detail,
            }
            for address in customer.addresses.filter(is_active=True).order_by("id")
        ],
    }


WORKBENCH_ACTION_IDS = (
    "register_payment",
    "verify_payment",
    "ship",
    "deliver_on_site",
    "receive",
    "review_commission",
    "complete_commission_adjustment",
    "cost_correction_commission_decision",
)

WORKBENCH_ACTION_LABELS = {
    "register_payment": "登记或补正收款",
    "verify_payment": "核对收款",
    "ship": "确认发货",
    "deliver_on_site": "确认现场交付",
    "receive": "确认收货",
    "review_commission": "复核提成",
    "complete_commission_adjustment": "确认提成差额已线下处理",
    "cost_correction_commission_decision": "确认成本纠错提成差额",
}

ORDER_OPERATION_LABELS = {
    "sales_order_create": "创建订单",
    "sales_order_amend": "修改订单",
    "sales_order_cancel": "取消订单",
    "payment_register": "登记或补正收款",
    "payment_verify": "核对收款",
    "sales_order_ship": "确认发货",
    "sales_order_onsite_delivery": "确认现场交付",
    "sales_order_receive": "确认收货",
    "sales_order_freight": "记录出库运费",
    "commission_review": "复核提成",
    "commission_adjustment_complete": "确认提成差额已线下处理",
    "cost_correction_commission_decision": "确认成本纠错提成差额",
}


def _order_action_state(actor, order, actor_roles=None):
    actor_roles = actor_roles if actor_roles is not None else set(actor.groups.values_list("name", flat=True))
    payment = getattr(order, "payment_record", None)
    review = getattr(order, "commission_review", None)
    commission_visible = sales_order_commission_visible_to(actor, order)
    own_order = actor.pk == order.salesperson_id
    can_change = sales_order_actor_can_change(actor, order, actor_roles)
    can_fulfill = sales_order_actor_can_fulfill(actor, order, actor_roles)
    before_fulfillment = order.status in (SalesOrder.Status.AWAITING_PAYMENT, SalesOrder.Status.READY_TO_SHIP)
    payment_current = payment is not None and payment.expected_payment_version == order.payment_version
    payment_has_images = payment is not None and bool(payment.images.all())
    payment_can_fulfill = (
        payment_current
        and payment_has_images
        and not payment.paused
        and payment.status != PaymentRecord.Status.PROBLEM
    )
    commission_payment_passed = (
        payment_current
        and payment.status == PaymentRecord.Status.PASSED
        and not payment.paused
        and payment.verified_version == order.payment_version
    )
    actions = []

    if can_change and before_fulfillment and actor.has_perm("crm.change_salesorder"):
        actions.append("amend_order")
    if can_change and before_fulfillment and actor.has_perm("crm.cancel_salesorder"):
        actions.append("cancel_order")
    if (
        can_change
        and actor.has_perm("crm.register_payment")
        and order.status != SalesOrder.Status.CANCELLED
        and (payment is None or payment.status == PaymentRecord.Status.PROBLEM or not payment_current)
    ):
        actions.append("register_payment")
    elif (
        can_change
        and actor.has_perm("crm.register_payment")
        and order.status != SalesOrder.Status.CANCELLED
        and payment is not None
    ):
        actions.append("update_payment")
    if (
        actor.has_perm("crm.verify_payment")
        and payment is not None
        and payment.status == PaymentRecord.Status.PENDING
        and payment_current
        and _can_verify(actor, payment.account)
    ):
        actions.append("verify_payment")
    if (
        can_fulfill
        and actor.has_perm("crm.ship_salesorder")
        and order.delivery_mode == SalesOrder.DeliveryMode.SHIPMENT
        and order.status == SalesOrder.Status.READY_TO_SHIP
        and payment_can_fulfill
    ):
        actions.append("ship")
    if (
        can_fulfill
        and actor.has_perm("crm.deliver_on_site")
        and order.delivery_mode == SalesOrder.DeliveryMode.ON_SITE
        and order.status == SalesOrder.Status.READY_TO_SHIP
        and payment_can_fulfill
    ):
        actions.append("deliver_on_site")
    if (
        own_order
        and actor.has_perm("crm.confirm_salesorder_receipt")
        and order.delivery_mode == SalesOrder.DeliveryMode.SHIPMENT
        and order.status == SalesOrder.Status.SHIPPED
    ):
        actions.append("receive")
    freight_permission = "deliver_on_site" if order.delivery_mode == SalesOrder.DeliveryMode.ON_SITE else "ship_salesorder"
    if (
        can_fulfill
        and actor.has_perm(f"crm.{freight_permission}")
        and order.status in (SalesOrder.Status.SHIPPED, SalesOrder.Status.COMPLETED)
    ):
        actions.append("update_freight")

    owner_or_admin = actor.is_superuser or ROLE_OWNER in actor_roles
    may_decide_cost_correction = (
        owner_or_admin
        and actor.has_perm("crm.view_costcorrection")
        and commission_visible
    )
    cost_suggestion = None
    may_need_commission_decision = actor.has_perm("crm.review_commission") or may_decide_cost_correction
    if (
        may_need_commission_decision
        and order.status == SalesOrder.Status.COMPLETED
        and commission_payment_passed
        and review is not None
    ):
        cost_suggestion = _current_cost_correction_commission_suggestion(order, review)
    review_is_current = review is not None and review.approved_amount_version == order.amount_version
    if (
        actor.has_perm("crm.review_commission")
        and order.status == SalesOrder.Status.COMPLETED
        and commission_payment_passed
        and not review_is_current
        and cost_suggestion is None
    ):
        actions.append("review_commission")

    pending_adjustments = []
    if own_order and actor.has_perm("crm.complete_commission_adjustment") and commission_visible:
        pending_adjustments = sorted(
            (row for row in order.commission_adjustments.all() if row.completed_at is None),
            key=lambda row: row.pk,
        )
        if pending_adjustments:
            actions.append("complete_commission_adjustment")
    if may_decide_cost_correction and cost_suggestion is not None:
        actions.append("cost_correction_commission_decision")

    return {
        "actions": actions,
        "payment": payment,
        "commission": review if commission_visible else None,
        "commission_visible": commission_visible,
        "pending_adjustments": pending_adjustments,
        "cost_correction_suggestion": cost_suggestion,
    }


def _sales_order_data(order, actor, action_state=None, actor_roles=None):
    action_state = action_state or _order_action_state(actor, order, actor_roles)
    payment = getattr(order, "payment_record", None)
    commission_visible = action_state["commission_visible"]
    commission = action_state["commission"]
    data = {
        "id": order.pk,
        "order_number": order.order_number,
        "sequence": order.last_sequence,
        "salesperson_id": order.salesperson_id,
        "salesperson": order.salesperson.get_username(),
        "customer_id": order.customer_id,
        "customer_name": order.customer_name_snapshot,
        "channel_id": order.channel_id,
        "channel": order.channel.name,
        "status": order.status,
        "status_label": order.get_status_display(),
        "delivery_mode": order.delivery_mode,
        "recipient": order.recipient_snapshot,
        "contact": order.contact_snapshot,
        "province": order.province_snapshot,
        "city": order.city_snapshot,
        "detail": order.detail_snapshot,
        "remarks": order.remarks,
        "requires_invoice": order.requires_invoice,
        "invoice_title": order.invoice_title,
        "invoice_credit_code": order.invoice_credit_code,
        "invoice_email": order.invoice_email,
        "revenue_amount": f"{order.revenue_amount:.3f}",
          "cost_basis": order.cost_basis,
          "cost_amount": f"{order.cost_amount:.3f}" if order.cost_amount is not None else None,
          "margin_amount": f"{order.margin_amount:.3f}" if order.margin_amount is not None else None,
      "version": order.version,
      "amount_version": order.amount_version,
      "payment_version": order.payment_version,
        "allowed_actions": action_state["actions"],
      "payment": None if payment is None else {
          "account_id": payment.account_id,
          "account_name": payment.account.name,
          "account_method_label": payment.account.get_method_display(),
          "account_responsibility_label": payment.account.get_responsibility_display(),
          "expected_amount": f"{payment.expected_amount:.3f}",
          "received_amount": f"{payment.received_amount:.3f}",
          "expected_payment_version": payment.expected_payment_version,
          "verified_version": payment.verified_version,
          "status": payment.status,
          "status_label": payment.get_status_display(),
          "paused": payment.paused,
          "difference_reason": payment.difference_reason,
          "problem_reason": payment.problem_reason,
          "received_at": payment.received_at.isoformat() if payment.received_at else None,
            "image_ids": [image.pk for image in payment.images.all()],
          "version": payment.version,
      },
      "shipments": [
          {
              "id": shipment.pk,
              "carrier": shipment.carrier,
              "carrier_label": shipment.get_carrier_display(),
              "tracking_number": shipment.tracking_number,
              "warehouses": [{"id": warehouse.pk, "name": warehouse.name} for warehouse in shipment.warehouses.all()],
          }
          for shipment in order.shipments.prefetch_related("warehouses").all()
      ],
      "shipped_at": order.shipped_at.isoformat() if order.shipped_at else None,
      "completed_at": order.completed_at.isoformat() if order.completed_at else None,
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
                  "unit_cost_snapshot": f"{line.unit_cost_snapshot:.3f}" if line.unit_cost_snapshot is not None else None,
                    "order_cost_amount": f"{line.order_cost_amount:.3f}" if line.order_cost_amount is not None else None,
                  "outbound_freight": f"{line.outbound_freight:.3f}",
                "cost_mode": line.cost_mode,
            }
            for line in order.lines.filter(active=True).select_related("sku", "warehouse").order_by("line_number")
      ],
    }
    if commission_visible:
        data.update({
              "commission_estimate": f"{order.commission_estimate:.3f}" if order.commission_estimate is not None else None,
            "commission_review": None if commission is None else {
                "approved_amount": f"{commission.approved_amount:.3f}" if commission.approved_amount is not None else None,
                "approved_amount_version": commission.approved_amount_version,
                "first_earned_at": commission.first_earned_at.isoformat() if commission.first_earned_at else None,
                "reviewed_at": commission.reviewed_at.isoformat() if commission.reviewed_at else None,
                "needs_review": commission.approved_amount is None or commission.approved_amount_version != order.amount_version,
            },
            "commission_adjustments": [
                {
                    "id": adjustment.pk,
                    "kind": adjustment.kind,
                    "kind_label": adjustment.get_kind_display(),
                    "old_amount": f"{adjustment.old_amount:.3f}",
                    "new_amount": f"{adjustment.new_amount:.3f}",
                    "delta": f"{adjustment.delta:.3f}",
                    "confirmed_at": adjustment.confirmed_at.isoformat(),
                    "completed_at": adjustment.completed_at.isoformat() if adjustment.completed_at else None,
                }
                for adjustment in order.commission_adjustments.all()
            ],
        })
    return data


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
    if include_archived not in ("true", "false"):
        return _api_error(request, 400, "invalid_filter", "筛选参数须为true或false。")
    include_created_today = request.GET.get("include_created_today", "false")
    if include_created_today not in ("true", "false"):
        return _api_error(request, 400, "invalid_filter", "筛选参数须为true或false。")
    items = customer_queryset_for(request.user).select_related("owner")
    if include_archived == "false":
        items = items.filter(is_active=True)
    owner_rows = (
        items.filter(owner_id__isnull=False)
        .order_by("owner__username", "owner_id")
        .values_list("owner_id", "owner__username")
        .distinct()
    )
    filter_options = {
        "salespeople": [{"id": str(owner_id), "label": username} for owner_id, username in owner_rows]
    }
    owner_value = request.GET.get("owner_id", "")
    if owner_value:
        try:
            owner_id = identifier(owner_value, "owner_id")
        except OperationError as exc:
            return _api_error(request, exc.status, exc.code, str(exc), exc.fields)
        items = items.filter(owner_id=owner_id)
    query = request.GET.get("q", "").strip()
    if query:
        items = items.filter(
            Q(name__icontains=query) | Q(company__icontains=query)
            | Q(contact__icontains=query) | Q(aliases__icontains=query)
        )
    created_today_total = None
    if include_created_today == "true":
        now = timezone.localtime()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        created_today_total = items.filter(created_at__gte=today_start, created_at__lte=now).count()
    try:
        page = paginate(items.order_by("name", "id"), request.GET)
    except CatalogQueryError as exc:
        return _api_error(request, 400, "invalid_pagination", str(exc), {"page": str(exc)})
    page["items"] = [_customer_data(item) for item in page["items"]]
    if created_today_total is not None:
        page["created_today_total"] = created_today_total
    page["filter_options"] = filter_options
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
    order_scope = sales_order_queryset_for(request.user)
    salesperson_rows = (
        order_scope.order_by()
        .values_list("salesperson_id", "salesperson__username")
        .distinct()
        .order_by("salesperson__username", "salesperson_id")
    )
    warehouse_rows = (
        SalesOrderLine.objects.filter(order_id__in=order_scope.values("pk"), active=True)
        .values_list("warehouse_id", "warehouse__name")
        .distinct()
        .order_by("warehouse__name", "warehouse_id")
    )
    filter_options = {
        "salespeople": [{"id": str(user_id), "label": username} for user_id, username in salesperson_rows],
        "warehouses": [{"id": str(warehouse_id), "label": name} for warehouse_id, name in warehouse_rows],
    }
    items = order_scope.select_related(
        "salesperson", "customer", "channel", "payment_record__account", "commission_review",
    ).prefetch_related("payment_record__images", "commission_adjustments")
    status_value = request.GET.get("status", "")
    if status_value:
        if status_value not in SalesOrder.Status.values:
            return _api_error(request, 400, "invalid_filter", "订单状态无效。", {"status": "请选择支持的订单状态。"})
        items = items.filter(status=status_value)
    customer_value = request.GET.get("customer_id", "")
    if customer_value != "":
        error = _require_api_access(request, (("crm", "view_customer"),))
        if error:
            return error
        try:
            customer_id = identifier(customer_value, "customer_id")
        except OperationError as exc:
            return _api_error(request, exc.status, exc.code, str(exc), exc.fields)
        customer = customer_queryset_for(request.user).filter(pk=customer_id).first()
        if customer is None:
            return _api_error(request, 404, "not_found", "客户不存在或不在可见范围。")
        items = items.filter(customer_id=customer_id)
    for field, lookup in (("warehouse_id", "lines__warehouse_id"), ("salesperson_id", "salesperson_id")):
        value = request.GET.get(field, "")
        if value == "":
            continue
        try:
            value = identifier(value, field)
        except OperationError as exc:
            return _api_error(request, exc.status, exc.code, str(exc), exc.fields)
        if field == "warehouse_id":
            items = items.filter(lines__active=True, **{lookup: value}).distinct()
        else:
            items = items.filter(**{lookup: value})
    start_date = request.GET.get("start_date", "")
    end_date = request.GET.get("end_date", "")
    if start_date or end_date:
        try:
            period = _date_criteria({"period": "custom", "start_date": start_date, "end_date": end_date})
        except OperationError as exc:
            return _api_error(request, exc.status, exc.code, str(exc), exc.fields)
        items = items.filter(created_at__gte=period["start_at"], created_at__lt=period["end_before"])
    verification_status = request.GET.get("verification_status", "")
    if verification_status:
        if verification_status not in PaymentRecord.Status.values:
            return _api_error(
                request,
                400,
                "invalid_filter",
                "收款核对状态无效。",
                {"verification_status": "请选择pending、problem或passed。"},
            )
        items = items.filter(payment_record__status=verification_status)
    query = request.GET.get("q", "").strip()
    if query:
        search = (
            Q(customer_name_snapshot__icontains=query)
            | Q(channel__name__icontains=query)
            | Q(salesperson__username__icontains=query)
        )
        if query.isascii() and query.isdigit():
            search |= Q(order_number=query)
        if query.isascii() and query.isdigit() and len(query) <= 19:
            order_number = int(query)
            if order_number <= 2**63 - 1:
                search |= Q(last_sequence=order_number) | Q(pk=order_number)
        items = items.filter(search)
    try:
        page = paginate(items.order_by("-last_sequence", "-id"), request.GET)
    except CatalogQueryError as exc:
        return _api_error(request, 400, "invalid_pagination", str(exc), {"page": str(exc)})
    actor_roles = set(request.user.groups.values_list("name", flat=True))
    page["items"] = [_sales_order_data(item, request.user, actor_roles=actor_roles) for item in page["items"]]
    page["filter_options"] = filter_options
    page["queried_at"] = timezone.localtime().isoformat(timespec="seconds")
    return JsonResponse(page)


def _workbench_task_item(order, action_id, action_state, *, adjustment=None):
    lines = getattr(order, "_workbench_lines", None)
    if lines is None:
        lines = list(order.lines.filter(active=True).select_related("sku__product").order_by("line_number"))
    product_rows = [
        {
            "sku_code": line.sku.code,
            "product_name": line.sku.product.name,
            "unit": line.sku.unit,
            "quantity": line.quantity,
        }
        for line in lines[:3]
    ]
    payment = action_state["payment"]
    payment_context = None
    if payment is not None:
        payment_context = {
            "account_id": payment.account_id,
            "account_name": payment.account.name,
            "account_responsibility": payment.account.get_responsibility_display(),
            "status": payment.status,
            "paused": payment.paused,
            "expected_amount": f"{payment.expected_amount:.3f}",
            "received_amount": f"{payment.received_amount:.3f}",
            "has_receipt": bool(payment.images.all()),
        }
        if action_id == "register_payment":
            payment_context.update({
                "problem_reason": payment.problem_reason,
                "difference_reason": payment.difference_reason,
            })
    item = {
        "id": f"{action_id}:{order.pk}:{adjustment.pk}" if adjustment else f"{action_id}:{order.pk}",
        "action_id": action_id,
        "action_label": WORKBENCH_ACTION_LABELS[action_id],
        "order_id": order.pk,
        "order_number": order.order_number,
        "order_sequence": order.last_sequence,
        "summary": {
            "customer_name": order.customer_name_snapshot,
            "products": product_rows,
            "revenue_amount": f"{order.revenue_amount:.3f}",
        },
        "status": {
            "order": order.status,
            "order_label": order.get_status_display(),
            "payment": payment.status if payment else None,
            "payment_label": payment.get_status_display() if payment else None,
            "payment_paused": payment.paused if payment else False,
        },
        "context": {"payment": payment_context},
        "target": {
            "type": "order_action",
            "page": f"/orders/{order.pk}/",
            "order_id": order.pk,
            "action_id": action_id,
        },
    }
    if action_id == "review_commission" and action_state["commission"] is not None:
        review = action_state["commission"]
        item["context"]["commission"] = {
            "calculated_amount": f"{order.commission_estimate:.3f}" if order.commission_estimate is not None else None,
            "approved_amount": f"{review.approved_amount:.3f}" if review.approved_amount is not None else None,
            "approved_amount_version": review.approved_amount_version,
        }
    if adjustment is not None:
        item["adjustment_id"] = adjustment.pk
        item["context"]["commission_adjustment"] = {
            "kind": adjustment.kind,
            "delta": f"{adjustment.delta:.3f}",
            "confirmed_at": adjustment.confirmed_at.isoformat(),
        }
    if action_id == "cost_correction_commission_decision":
        correction, _suggestion = action_state["cost_correction_suggestion"]
        item["id"] = f"{action_id}:{order.pk}:{correction.pk}"
        item["target"] = {
            "type": "cost_correction_commission_suggestion",
            "page": "/adjustments/",
            "order_id": order.pk,
            "cost_correction_id": correction.pk,
        }
    return item


@require_GET
def workbench_tasks_api(request):
    error = _require_api_access(request, (("crm", "view_salesorder"),))
    if error:
        return error
    try:
        paginate([], request.GET)
    except CatalogQueryError as exc:
        return _api_error(request, 400, "invalid_pagination", str(exc), {"page": str(exc)})

    orders = (
        sales_order_queryset_for(request.user)
        .select_related("salesperson", "customer", "channel", "payment_record__account", "commission_review")
        .prefetch_related(
            Prefetch(
                "lines",
                queryset=SalesOrderLine.objects.filter(active=True).select_related("sku__product").order_by("line_number"),
                to_attr="_workbench_lines",
            ),
            "payment_record__images",
            "commission_adjustments",
        )
        .order_by("-last_sequence", "-pk")
    )
    tasks = []
    actor_roles = set(request.user.groups.values_list("name", flat=True))
    # shortcut: scans visible orders so counts match pagination; switch to grouped queries if one request outgrows the ledger.
    for order in orders:
        action_state = _order_action_state(request.user, order, actor_roles)
        for action_id in action_state["actions"]:
            if action_id not in WORKBENCH_ACTION_IDS:
                continue
            if action_id == "complete_commission_adjustment":
                tasks.extend(
                    _workbench_task_item(order, action_id, action_state, adjustment=row)
                    for row in action_state["pending_adjustments"]
                )
            else:
                tasks.append(_workbench_task_item(order, action_id, action_state))

    counts = {action_id: 0 for action_id in WORKBENCH_ACTION_IDS}
    for task in tasks:
        counts[task["action_id"]] += 1
    try:
        page = paginate(tasks, request.GET)
    except CatalogQueryError as exc:
        return _api_error(request, 400, "invalid_pagination", str(exc), {"page": str(exc)})
    page["counts"] = counts
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
    actor_roles = set(request.user.groups.values_list("name", flat=True))
    return JsonResponse(_sales_order_data(order, request.user, actor_roles=actor_roles))


@require_GET
def order_history_api(request, order_id):
    error = _require_api_access(request, (("crm", "view_salesorder"),))
    if error:
        return error
    order = sales_order_queryset_for(request.user).filter(pk=order_id).first()
    if order is None:
        return _api_error(request, 404, "not_found", "订单不存在或不在可见范围。")
    operations = (
        BusinessOperation.objects.filter(sales_order=order)
        .select_related("actor")
        .order_by("-sequence", "-pk")
    )
    try:
        page = paginate(operations, request.GET)
    except CatalogQueryError as exc:
        return _api_error(request, 400, "invalid_pagination", str(exc), {"page": str(exc)})
    page["items"] = [
        {
            "id": operation.pk,
            "sequence": operation.sequence,
            "action_label": ORDER_OPERATION_LABELS.get(operation.action, "未分类订单操作"),
            "actor_name": operation.actor.get_username(),
            "created_at": timezone.localtime(operation.created_at).isoformat(timespec="seconds"),
        }
        for operation in page["items"]
    ]
    return JsonResponse(page)


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


@require_safe
def payment_accounts_api(request):
    error = _require_api_access(request, (("crm", "view_paymentaccount"),))
    if error:
        return error
    return JsonResponse({
        "items": [
            {
                "id": item.pk,
                "name": item.name,
                "method": item.method,
                "method_label": item.get_method_display(),
                "responsibility": item.responsibility,
                "responsibility_label": item.get_responsibility_display(),
            }
            for item in PaymentAccount.objects.filter(is_active=True).order_by("name", "id")
        ]
    })


@require_POST
def order_payment_api(request, order_id):
    error = _require_api_access(request, (("crm", "register_payment"),))
    if error:
        return error
    return _operation_error_response(request, lambda: _api_operation(request, register_payment, request.user, order_id, _json_body(request)))


@require_POST
def order_payment_verify_api(request, order_id):
    error = _require_api_access(request, (("crm", "verify_payment"),))
    if error:
        return error
    return _operation_error_response(request, lambda: _api_operation(request, verify_payment, request.user, order_id, _json_body(request)))


@require_POST
def order_ship_api(request, order_id):
    error = _require_api_access(request, (("crm", "ship_salesorder"),))
    if error:
        return error
    return _operation_error_response(request, lambda: _api_operation(request, ship_sales_order, request.user, order_id, _json_body(request)))


@require_POST
def order_deliver_on_site_api(request, order_id):
    error = _require_api_access(request, (("crm", "deliver_on_site"),))
    if error:
        return error
    return _operation_error_response(request, lambda: _api_operation(request, deliver_on_site, request.user, order_id, _json_body(request)))


@require_POST
def order_receive_api(request, order_id):
    error = _require_api_access(request, (("crm", "confirm_salesorder_receipt"),))
    if error:
        return error
    return _operation_error_response(request, lambda: _api_operation(request, receive_sales_order, request.user, order_id, _json_body(request)))


@require_POST
def order_freight_api(request, order_id):
    if not request.user.is_authenticated:
        return _api_error(request, 401, "authentication_required", "请先登录。")
    return _operation_error_response(request, lambda: _api_operation(request, update_outbound_freight, request.user, order_id, _json_body(request)))


@require_POST
def order_commission_review_api(request, order_id):
    error = _require_api_access(request, (("crm", "review_commission"),))
    if error:
        return error
    return _operation_error_response(request, lambda: _api_operation(request, review_commission, request.user, order_id, _json_body(request)))


@require_POST
def order_commission_adjustment_confirm_api(request, order_id):
    error = _require_api_access(request, ())
    if error:
        return error
    return _operation_error_response(
        request,
        lambda: _api_operation(
            request, confirm_cost_correction_commission_decision,
            request.user, order_id, _json_body(request),
        ),
    )


@require_POST
def commission_adjustment_complete_api(request, adjustment_id):
    error = _require_api_access(request, (("crm", "complete_commission_adjustment"),))
    if error:
        return error
    return _operation_error_response(
        request,
        lambda: _api_operation(request, complete_commission_adjustment, request.user, adjustment_id, _json_body(request)),
    )


def _require_page_permission(request, codename):
    if not request.user.is_superuser and not request.user.has_perm(f"crm.{codename}"):
        raise PermissionDenied


def _customer_owner_options(actor):
    is_owner_or_admin = actor.is_superuser or actor.groups.filter(name=ROLE_OWNER).exists()
    is_sales = actor.groups.filter(name=ROLE_SALES).exists()
    can_select_owner = is_owner_or_admin or not is_sales or actor.has_perm("crm.change_customer_owner")
    owners = customer_owner_queryset(get_user_model()).order_by("username") if can_select_owner else []
    return can_select_owner, owners


@login_required
@require_safe
def customers_page(request):
    _require_page_permission(request, "view_customer")
    can_select_owner, salespeople = _customer_owner_options(request.user)
    return render(request, "crm/customers.html", {"salespeople": salespeople, "can_select_owner": can_select_owner})


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
    can_select_owner, salespeople = _customer_owner_options(request.user)
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
def orders_page(request):
    _require_page_permission(request, "view_salesorder")
    return render(request, "crm/orders.html", {"status_options": SalesOrder.Status.choices})


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
        initial = _sales_order_data(order, request.user)
    can_select_owner, salespeople = _customer_owner_options(request.user)
    return render(request, "crm/order_form.html", {"order": order, "initial": initial, "salespeople": salespeople, "can_select_owner": can_select_owner})


@login_required
@require_safe
def order_detail_page(request, order_id):
    _require_page_permission(request, "view_salesorder")
    order = sales_order_queryset_for(request.user).select_related("salesperson", "customer", "channel").filter(pk=order_id).first()
    if order is None:
        raise PermissionDenied
    can_view_commission = sales_order_commission_visible_to(request.user, order)
    actor_roles = set(request.user.groups.values_list("name", flat=True))
    action_state = _order_action_state(request.user, order, actor_roles)
    initial = _sales_order_data(order, request.user, action_state)
    commission = getattr(order, "commission_review", None) if can_view_commission else None
    return render(request, "crm/order_detail.html", {
        "order": order,
        "initial": initial,
        "first_earned_at_display": timezone.localtime(commission.first_earned_at) if commission and commission.first_earned_at else None,
        "can_view_commission": can_view_commission,
        "allowed_actions": action_state["actions"],
        "can_edit_sales_order": "amend_order" in action_state["actions"],
        "can_cancel_sales_order": "cancel_order" in action_state["actions"],
        "can_register_payment": bool({"register_payment", "update_payment"} & set(action_state["actions"])),
        "can_confirm_salesorder_receipt": "receive" in action_state["actions"],
        "can_complete_commission_adjustment": (
            "complete_commission_adjustment" in action_state["actions"]
        ),
    })
