from django.contrib.auth.models import Group, Permission


ROLE_SALES = "销售"
ROLE_WAREHOUSE = "仓管／财务"
ROLE_OWNER = "老板"
ROLE_NAMES = (ROLE_SALES, ROLE_WAREHOUSE, ROLE_OWNER)

CATALOG_MODELS = ("brand", "product", "sku", "warehouse")

MANAGEABLE_PERMISSIONS = {
    ("crm", f"{action}_{model}")
    for model in CATALOG_MODELS
    for action in ("view", "add", "change")
} | {
    ("auth", f"{action}_user") for action in ("view", "add", "change")
} | {
    ("auth", f"{action}_group") for action in ("view", "change")
} | {("crm", "view_permissionchange")}

CATALOG_VIEW_PERMISSIONS = tuple(("crm", f"view_{model}") for model in CATALOG_MODELS)
CATALOG_CHANGE_PERMISSIONS = tuple(
    ("crm", f"{action}_{model}")
    for model in CATALOG_MODELS
    for action in ("add", "change")
)

ROLE_DEFAULTS = {
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
        if not created:
            continue
        permissions = [
            Permission.objects.get(content_type__app_label=app_label, codename=codename)
            for app_label, codename in permission_keys
        ]
        group.permissions.set(permissions)
