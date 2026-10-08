import unicodedata

from django.core.paginator import EmptyPage, Paginator
from django.db.models import Prefetch

from .models import Brand, Product, SKU, Warehouse


class CatalogQueryError(ValueError):
    pass


def _normalize(value):
    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()


def _matches(query, values):
    normalized = [_normalize(value) for value in values if value not in (None, "")]
    return any(value == query for value in normalized), any(query in value for value in normalized)


def _product_values(product):
    return [
        product.name,
        product.series,
        product.language,
        product.official_code,
        product.brand.name,
        *product.aliases,
    ]


def _parse_filters(params, *, include_sku_status=False):
    brand_id = params.get("brand_id", "").strip()
    if brand_id:
        try:
            brand_id = int(brand_id)
            if brand_id < 1:
                raise ValueError
        except ValueError as exc:
            raise CatalogQueryError("brand_id必须为正整数。") from exc
    type_value = params.get("type", "").strip()
    valid_types = {choice for choice, _ in Product.Type.choices}
    if type_value and type_value not in valid_types:
        raise CatalogQueryError("type不是有效商品类型。")
    filters = {
        "brand_id": brand_id or None,
        "series": params.get("series", "").strip(),
        "type": type_value,
        "language": params.get("language", "").strip(),
    }
    if include_sku_status:
        sku_status = params.get("sku_status", "").strip()
        if sku_status and sku_status not in {"active", "inactive", "all"}:
            raise CatalogQueryError("sku_status不是有效SKU状态。")
        filters["sku_status"] = sku_status or None
    return filters


def _product_queryset(params, *, include_skus=True, include_sku_status=False):
    filters = _parse_filters(params, include_sku_status=include_sku_status)
    queryset = Product.objects.filter(is_active=True, brand__is_active=True)
    if filters["brand_id"]:
        queryset = queryset.filter(brand_id=filters["brand_id"])
    if filters["series"]:
        queryset = queryset.filter(series__iexact=filters["series"])
    if filters["type"]:
        queryset = queryset.filter(type=filters["type"])
    if filters["language"]:
        queryset = queryset.filter(language__icontains=filters["language"])
    sku_status = filters.get("sku_status")
    sku_queryset = SKU.objects.all()
    if sku_status is None or sku_status == "active":
        sku_queryset = sku_queryset.filter(is_active=True)
    elif sku_status == "inactive":
        sku_queryset = sku_queryset.filter(is_active=False)
    if sku_status == "all":
        queryset = queryset.filter(skus__isnull=False).distinct()
    elif sku_status:
        queryset = queryset.filter(skus__is_active=(sku_status == "active")).distinct()
    queryset = queryset.select_related("brand")
    if include_skus:
        queryset = queryset.prefetch_related(
            Prefetch("skus", queryset=sku_queryset.order_by("id"))
        )
    return queryset


def _ranked_products(params):
    query = _normalize(params.get("q", ""))
    matches = []
    for product in _product_queryset(params, include_sku_status=True):
        values = _product_values(product)
        for sku in product.skus.all():
            values.extend((sku.code, sku.barcode, *sku.aliases))
        exact, partial = _matches(query, values) if query else (False, True)
        if partial:
            matches.append((not exact, product.pk, product))
    return [item for _, _, item in sorted(matches)]


def _ranked_skus(params):
    query = _normalize(params.get("q", ""))
    products = _product_queryset(params)
    product_ids = list(products.values_list("id", flat=True))
    matches = []
    for sku in (
        SKU.objects.filter(is_active=True, product_id__in=product_ids)
        .select_related("product__brand")
        .order_by("id")
    ):
        values = [
            sku.code,
            sku.barcode,
            sku.unit,
            sku.box_spec,
            sku.inner_box_spec,
            sku.pack_spec,
            *sku.aliases,
            *_product_values(sku.product),
        ]
        exact, partial = _matches(query, values) if query else (False, True)
        if partial:
            matches.append((not exact, sku.pk, sku))
    return [item for _, _, item in sorted(matches)]


def _sku_summary(sku):
    return {
        "id": sku.pk,
        "code": sku.code,
        "unit": sku.unit,
        "box_spec": sku.box_spec,
        "inner_box_spec": sku.inner_box_spec,
        "pack_spec": sku.pack_spec,
        "pack_type": sku.pack_type,
        "pack_type_label": sku.get_pack_type_display(),
        "barcode": sku.barcode or None,
        "aliases": sku.aliases,
        "is_active": sku.is_active,
    }


def product_data(product):
    return {
        "id": product.pk,
        "brand": {"id": product.brand_id, "name": product.brand.name},
        "type": product.type,
        "type_label": product.get_type_display(),
        "series": product.series,
        "name": product.name,
        "language": product.language,
        "official_code": product.official_code,
        "aliases": product.aliases,
        "skus": [_sku_summary(sku) for sku in product.skus.all()],
    }


def sku_data(sku):
    return {
        **_sku_summary(sku),
        "product": {
            "id": sku.product_id,
            "name": sku.product.name,
            "type": sku.product.type,
            "type_label": sku.product.get_type_display(),
            "series": sku.product.series,
            "language": sku.product.language,
            "official_code": sku.product.official_code,
            "aliases": sku.product.aliases,
        },
        "brand": {"id": sku.product.brand_id, "name": sku.product.brand.name},
    }


def search_products(params):
    return [product_data(item) for item in _ranked_products(params)]


def search_skus(params):
    return [sku_data(item) for item in _ranked_skus(params)]


def search_warehouses(params):
    query = _normalize(params.get("q", ""))
    matches = []
    for warehouse in Warehouse.objects.filter(is_active=True).order_by("id"):
        exact, partial = _matches(query, (warehouse.name, warehouse.address)) if query else (False, True)
        if partial:
            matches.append((not exact, warehouse.pk, warehouse))
    return [
        {"id": item.pk, "name": item.name, "address": item.address, "is_active": item.is_active}
        for _, _, item in sorted(matches)
    ]


def paginate(items, params):
    try:
        page = int(params.get("page", 1))
        page_size = int(params.get("page_size", 50))
    except (TypeError, ValueError) as exc:
        raise CatalogQueryError("page与page_size必须为整数。") from exc
    if page < 1 or page_size < 1 or page_size > 200:
        raise CatalogQueryError("page必须大于0，page_size须为1到200。")
    paginator = Paginator(items, page_size)
    try:
        result = paginator.page(page)
    except EmptyPage:
        return {
            "items": [],
            "total": paginator.count,
            "page": page,
            "page_size": page_size,
            "has_previous": page > 1,
            "has_next": False,
        }
    return {
        "items": list(result.object_list),
        "total": paginator.count,
        "page": result.number,
        "page_size": page_size,
        "has_previous": result.has_previous(),
        "has_next": result.has_next(),
    }
