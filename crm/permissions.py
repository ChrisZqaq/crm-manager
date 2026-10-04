from django.contrib.auth.models import Group, Permission


ROLE_SALES = "销售"
ROLE_WAREHOUSE = "仓管／财务"
ROLE_OWNER = "老板"
ROLE_NAMES = (ROLE_SALES, ROLE_WAREHOUSE, ROLE_OWNER)

CATALOG_MODELS = ("brand", "product", "sku", "warehouse")
SUPPLIER_PERMISSIONS = (("crm", "view_supplier"), ("crm", "add_supplier"), ("crm", "change_supplier"))
INVENTORY_VIEW_PERMISSIONS = (
    ("crm", "view_inventorybalance"),
    ("crm", "view_inventoryhold"),
    ("crm", "view_damagedrecord"),
)
RECEIPT_VIEW_PERMISSIONS = (("crm", "view_receipt"),)
WAREHOUSE_OPERATION_PERMISSIONS = (
    ("crm", "confirm_receipt"),
    ("crm", "confirm_opening"),
    ("crm", "create_inventory_hold"),
    ("crm", "release_inventory_hold"),
    ("crm", "manage_sku_low_stock"),
)
OWNER_OPERATION_PERMISSIONS = WAREHOUSE_OPERATION_PERMISSIONS + (("crm", "manage_global_low_stock"),)

MANAGEABLE_PERMISSIONS = {
    ("crm", f"{action}_{model}")
    for model in CATALOG_MODELS
    for action in ("view", "add", "change")
} | {
    ("auth", f"{action}_user") for action in ("view", "add", "change")
} | {
    ("auth", f"{action}_group") for action in ("view", "change")
} | {("crm", "view_permissionchange")} | set(SUPPLIER_PERMISSIONS) | {
    ("crm", codename)
    for codename in (
        "view_inventorybalance",
        "view_inventoryhold",
        "view_damagedrecord",
        "view_receipt",
        "confirm_receipt",
        "confirm_opening",
        "create_inventory_hold",
        "release_inventory_hold",
        "manage_sku_low_stock",
        "manage_global_low_stock",
    )
}

CATALOG_VIEW_PERMISSIONS = tuple(("crm", f"view_{model}") for model in CATALOG_MODELS)
CATALOG_CHANGE_PERMISSIONS = tuple(
    ("crm", f"{action}_{model}")
    for model in CATALOG_MODELS
    for action in ("add", "change")
)

ROLE_DEFAULTS = {
    ROLE_SALES: CATALOG_VIEW_PERMISSIONS + INVENTORY_VIEW_PERMISSIONS,
    ROLE_WAREHOUSE: (
        CATALOG_VIEW_PERMISSIONS
        + CATALOG_CHANGE_PERMISSIONS
        + SUPPLIER_PERMISSIONS
        + INVENTORY_VIEW_PERMISSIONS
        + RECEIPT_VIEW_PERMISSIONS
        + WAREHOUSE_OPERATION_PERMISSIONS
    ),
    ROLE_OWNER: CATALOG_VIEW_PERMISSIONS
      + CATALOG_CHANGE_PERMISSIONS
      + SUPPLIER_PERMISSIONS
      + INVENTORY_VIEW_PERMISSIONS
      + RECEIPT_VIEW_PERMISSIONS
      + OWNER_OPERATION_PERMISSIONS
      + (
        ("auth", "view_user"),
        ("auth", "add_user"),
        ("auth", "change_user"),
        ("auth", "view_group"),
        ("auth", "change_group"),
        ("crm", "view_permissionchange"),
    ),
}


PRE_STAGE2_ROLE_DEFAULTS = {
    ROLE_SALES: CATALOG_VIEW_PERMISSIONS,
    ROLE_WAREHOUSE: CATALOG_VIEW_PERMISSIONS + CATALOG_CHANGE_PERMISSIONS,
    ROLE_OWNER: CATALOG_VIEW_PERMISSIONS
    + CATALOG_CHANGE_PERMISSIONS
    + (
        ("auth", "view_user"),
        ("auth", "add_user"),
        ("auth", "change_user"),
        ("auth", "view_group"),
        ("auth", "change_group"),
        ("crm", "view_permissionchange"),
    ),
}


def create_fixed_role_groups(sender, **kwargs):
    for name, permission_keys in ROLE_DEFAULTS.items():
        group, created = Group.objects.get_or_create(name=name)
        if created:
            previous = set()
        else:
            previous = set(group.permissions.values_list("content_type__app_label", "codename"))
        if not created and previous != set(PRE_STAGE2_ROLE_DEFAULTS[name]):
            continue
        permissions = [
            Permission.objects.get(content_type__app_label=app_label, codename=codename)
            for app_label, codename in permission_keys
        ]
        group.permissions.set(permissions)


def ensure_business_control(sender, **kwargs):
    from .models import BusinessControl

    BusinessControl.objects.get_or_create(pk=1)
