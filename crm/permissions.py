from django.contrib.auth.models import Group, Permission


ROLE_SALES = "销售"
ROLE_WAREHOUSE = "仓管／财务"
ROLE_OWNER = "老板"
ROLE_NAMES = (ROLE_SALES, ROLE_WAREHOUSE, ROLE_OWNER)

CATALOG_MODELS = ("brand", "product", "sku", "warehouse")
CUSTOMER_ORDER_MODELS = ("customer", "salesorder", "channel")
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
OWNER_OPERATION_PERMISSIONS = WAREHOUSE_OPERATION_PERMISSIONS + (
    ("crm", "manage_global_low_stock"),
    ("crm", "manage_opening"),
)

MANAGEABLE_PERMISSIONS = {
    ("crm", f"{action}_{model}")
    for model in CATALOG_MODELS + CUSTOMER_ORDER_MODELS
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
          "view_stockcount",
          "view_costcorrection",
          "approve_stockcount",
          "add_stockcount",
          "create_inventory_split",
          "create_inventory_gift",
          "resolve_damaged_record",
          "submit_costcorrection",
          "approve_costcorrection",
          "reject_costcorrection",
        "view_receipt",
        "confirm_receipt",
        "confirm_opening",
        "create_inventory_hold",
        "release_inventory_hold",
        "manage_sku_low_stock",
          "manage_global_low_stock",
          "view_all_customers",
          "change_all_customers",
          "archive_customer",
          "change_customer_owner",
          "view_all_sales_orders",
          "change_all_sales_orders",
          "cancel_salesorder",
          "register_payment",
          "ship_salesorder",
          "deliver_on_site",
          "confirm_salesorder_receipt",
          "verify_payment",
          "review_commission",
          "view_all_commissions",
          "complete_commission_adjustment",
          "view_paymentaccount",
          "add_paymentaccount",
          "change_paymentaccount",
      )
  }

CATALOG_VIEW_PERMISSIONS = tuple(("crm", f"view_{model}") for model in CATALOG_MODELS)
CATALOG_CHANGE_PERMISSIONS = tuple(
    ("crm", f"{action}_{model}")
    for model in CATALOG_MODELS
    for action in ("add", "change")
)

STAGE2_ROLE_DEFAULTS = {
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


SALES_CUSTOMER_ORDER_PERMISSIONS = (
    ("crm", "view_customer"),
    ("crm", "add_customer"),
    ("crm", "change_customer"),
    ("crm", "archive_customer"),
    ("crm", "view_salesorder"),
    ("crm", "add_salesorder"),
    ("crm", "change_salesorder"),
    ("crm", "cancel_salesorder"),
)
ROLE_DEFAULTS = {
    ROLE_SALES: STAGE2_ROLE_DEFAULTS[ROLE_SALES]
    + SALES_CUSTOMER_ORDER_PERMISSIONS
    + (("crm", "view_channel"),),
    ROLE_WAREHOUSE: STAGE2_ROLE_DEFAULTS[ROLE_WAREHOUSE]
    + (("crm", "view_channel"), ("crm", "view_salesorder"), ("crm", "view_all_sales_orders"), ("crm", "add_customer")),
    ROLE_OWNER: STAGE2_ROLE_DEFAULTS[ROLE_OWNER]
    + SALES_CUSTOMER_ORDER_PERMISSIONS
    + (
        ("crm", "view_channel"),
        ("crm", "add_channel"),
        ("crm", "change_channel"),
        ("crm", "view_all_customers"),
        ("crm", "change_all_customers"),
        ("crm", "change_customer_owner"),
        ("crm", "view_all_sales_orders"),
        ("crm", "change_all_sales_orders"),
    ),
}

PRE_STAGE4_ROLE_DEFAULTS = {name: tuple(permission_keys) for name, permission_keys in ROLE_DEFAULTS.items()}
ROLE_DEFAULTS = {
    ROLE_SALES: PRE_STAGE4_ROLE_DEFAULTS[ROLE_SALES]
    + (
        ("crm", "view_paymentaccount"),
        ("crm", "register_payment"),
        ("crm", "confirm_salesorder_receipt"),
        ("crm", "complete_commission_adjustment"),
    ),
    ROLE_WAREHOUSE: PRE_STAGE4_ROLE_DEFAULTS[ROLE_WAREHOUSE]
    + (
        ("crm", "view_paymentaccount"),
        ("crm", "verify_payment"),
        ("crm", "ship_salesorder"),
        ("crm", "deliver_on_site"),
        ("crm", "view_all_commissions"),
    ),
    ROLE_OWNER: PRE_STAGE4_ROLE_DEFAULTS[ROLE_OWNER]
    + (
        ("crm", "view_paymentaccount"),
        ("crm", "add_paymentaccount"),
        ("crm", "change_paymentaccount"),
        ("crm", "verify_payment"),
        ("crm", "ship_salesorder"),
        ("crm", "deliver_on_site"),
        ("crm", "confirm_salesorder_receipt"),
        ("crm", "register_payment"),
        ("crm", "review_commission"),
        ("crm", "view_all_commissions"),
        ("crm", "complete_commission_adjustment"),
    ),
}

PRE_D14_WAREHOUSE_DEFAULTS = STAGE2_ROLE_DEFAULTS[ROLE_WAREHOUSE] + (
    ("crm", "view_channel"),
    ("crm", "view_salesorder"),
    ("crm", "view_all_sales_orders"),
)

PRE_STAGE5_ROLE_DEFAULTS = {name: tuple(permission_keys) for name, permission_keys in ROLE_DEFAULTS.items()}
STAGE5_WAREHOUSE_PERMISSIONS = (
    ("crm", "view_stockcount"),
    ("crm", "view_costcorrection"),
    ("crm", "create_inventory_split"),
    ("crm", "create_inventory_gift"),
    ("crm", "resolve_damaged_record"),
    ("crm", "add_stockcount"),
    ("crm", "submit_costcorrection"),
)
STAGE5_OWNER_PERMISSIONS = (
    ("crm", "approve_stockcount"),
    ("crm", "approve_costcorrection"),
    ("crm", "reject_costcorrection"),
)
ROLE_DEFAULTS = {
    ROLE_SALES: ROLE_DEFAULTS[ROLE_SALES],
    ROLE_WAREHOUSE: ROLE_DEFAULTS[ROLE_WAREHOUSE] + STAGE5_WAREHOUSE_PERMISSIONS,
    ROLE_OWNER: ROLE_DEFAULTS[ROLE_OWNER] + STAGE5_WAREHOUSE_PERMISSIONS + STAGE5_OWNER_PERMISSIONS,
}


def create_fixed_role_groups(sender, **kwargs):
    for name, permission_keys in ROLE_DEFAULTS.items():
        group, created = Group.objects.get_or_create(name=name)
        if created:
            previous = set()
        else:
            previous = set(group.permissions.values_list("content_type__app_label", "codename"))
        recognized_defaults = {
            frozenset(PRE_STAGE2_ROLE_DEFAULTS[name]),
            frozenset(STAGE2_ROLE_DEFAULTS[name]),
          frozenset(PRE_STAGE4_ROLE_DEFAULTS[name]),
          frozenset(PRE_STAGE5_ROLE_DEFAULTS[name]),
        }
        if name == ROLE_WAREHOUSE:
            recognized_defaults.add(frozenset(PRE_D14_WAREHOUSE_DEFAULTS))
        if not created and frozenset(previous) not in recognized_defaults:
            continue
        permissions = [
            Permission.objects.get(content_type__app_label=app_label, codename=codename)
            for app_label, codename in permission_keys
        ]
        group.permissions.set(permissions)


def ensure_business_control(sender, **kwargs):
    from .models import BusinessControl

    BusinessControl.objects.get_or_create(pk=1)
