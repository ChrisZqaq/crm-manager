import uuid

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_safe

from .catalog import CatalogQueryError, paginate, search_products, search_skus, search_warehouses
from .models import Brand
from .permissions import CATALOG_VIEW_PERMISSIONS


def home(request):
    return redirect("catalog-search")


def _request_id(request):
    return request.headers.get("X-Request-ID", "")[:80] or str(uuid.uuid4())


def _api_error(request, status, code, message, fields=None):
    return JsonResponse(
        {
            "error": {"code": code, "message": message, "fields": fields or {}, "details": {}},
            "request_id": _request_id(request),
        },
        status=status,
    )


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
