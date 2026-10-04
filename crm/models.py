from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


def validate_aliases(value):
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValidationError("别名必须是字符串列表。")


class Brand(models.Model):
    name = models.CharField("IP名称", max_length=120, unique=True)
    sku_prefix = models.CharField("SKU编号前缀", max_length=32, unique=True)
    is_active = models.BooleanField("启用", default=True)

    class Meta:
        ordering = ("id",)

    def clean(self):
        self.name = self.name.strip()
        self.sku_prefix = self.sku_prefix.strip()
        if not self.name:
            raise ValidationError({"name": "IP名称不能为空。"})
        if not self.sku_prefix or any(char.isspace() for char in self.sku_prefix):
            raise ValidationError({"sku_prefix": "请填写不含空格的IP编号前缀。"})
        if self.pk:
            previous = Brand.objects.filter(pk=self.pk).values_list("sku_prefix", flat=True).first()
            if previous != self.sku_prefix and self.products.filter(skus__isnull=False).exists():
                raise ValidationError({"sku_prefix": "该IP已有SKU，不能修改前缀。"})

    def __str__(self):
        return self.name


class Product(models.Model):
    class Type(models.TextChoices):
        BOOSTER = "booster", "补充包"
        MERCHANDISE = "merchandise", "周边"
        GIFT_SET = "gift_set", "礼盒·预组"

    brand = models.ForeignKey(Brand, verbose_name="IP", on_delete=models.PROTECT, related_name="products")
    type = models.CharField("商品类型", max_length=16, choices=Type.choices)
    series = models.CharField("系列", max_length=120, blank=True)
    name = models.CharField("产品名", max_length=255)
    language = models.CharField("语言版本", max_length=80, blank=True)
    official_code = models.CharField("官方编号", max_length=100, blank=True)
    aliases = models.JSONField("产品别名", default=list, blank=True, validators=[validate_aliases])
    is_active = models.BooleanField("启用", default=True)

    class Meta:
        ordering = ("id",)
        constraints = [
            models.CheckConstraint(
                condition=Q(type__in=["booster", "merchandise", "gift_set"]),
                name="crm_product_type_valid",
            ),
            models.CheckConstraint(
                condition=~Q(type__in=["booster", "gift_set"]) | Q(language__gt=""),
                name="crm_product_language_required",
            ),
        ]

    def clean(self):
        self.series = self.series.strip()
        self.name = self.name.strip()
        self.language = self.language.strip()
        self.official_code = self.official_code.strip()
        self.aliases = [value.strip() for value in self.aliases if value.strip()]
        self.aliases = list(dict.fromkeys(self.aliases))

        errors = {}
        if not self.name:
            errors["name"] = "产品名不能为空。"
        if self.type in (self.Type.BOOSTER, self.Type.GIFT_SET) and not self.language:
            errors["language"] = "补充包及礼盒·预组必须填写语言版本。"
        if errors:
            raise ValidationError(errors)

        if self.pk:
            previous_brand_id = Product.objects.filter(pk=self.pk).values_list("brand_id", flat=True).first()
            if previous_brand_id != self.brand_id and self.skus.exists():
                raise ValidationError({"brand": "已有SKU的产品不能更换所属IP。"})

    def __str__(self):
        parts = [self.name]
        if self.language:
            parts.append(self.language)
        return " · ".join(parts)


class SKU(models.Model):
    class PackType(models.TextChoices):
        SLIM = "slim", "瘦包"
        FAT = "fat", "肥包"
        EMPTY = "empty", "空"

    product = models.ForeignKey(Product, verbose_name="产品", on_delete=models.PROTECT, related_name="skus")
    code = models.CharField("内部SKU编号", max_length=255, unique=True)
    box_spec = models.TextField("箱规格说明", blank=True)
    inner_box_spec = models.TextField("盒规格说明", blank=True, default="")
    pack_spec = models.TextField("包规格说明", blank=True)
    pack_type = models.CharField("包类型", max_length=8, choices=PackType.choices, blank=True, default="")
    unit = models.CharField("售卖单位", max_length=80)
    barcode = models.CharField("包装条码", max_length=255, blank=True, db_index=True)
    aliases = models.JSONField("规格别名", default=list, blank=True, validators=[validate_aliases])
    low_stock_threshold = models.PositiveBigIntegerField("低库存阈值", null=True, blank=True)
    low_stock_enabled = models.BooleanField("启用低库存提醒", default=True)
    is_active = models.BooleanField("启用", default=True)

    class Meta:
        ordering = ("id",)
        permissions = (("manage_sku_low_stock", "管理SKU低库存提醒"),)

    def clean(self):
        self.code = self.code.strip()
        self.unit = self.unit.strip()
        self.barcode = self.barcode.strip()
        self.aliases = [value.strip() for value in self.aliases if value.strip()]
        self.aliases = list(dict.fromkeys(self.aliases))

        errors = {}
        if not self.unit:
            errors["unit"] = "售卖单位不能为空。"
        if self.product_id:
            prefix = self.product.brand.sku_prefix
            suffix = self.code[len(prefix):] if self.code.startswith(prefix) else ""
            if not suffix.isascii() or not suffix.isdigit() or len(suffix) < 4:
                errors["code"] = f"编号必须以该IP前缀 {prefix} 开头，后接至少4位数字流水号。"
        else:
            errors["product"] = "请选择所属产品。"
        if self.pk:
            previous = SKU.objects.filter(pk=self.pk).values_list("code", flat=True).first()
            if previous != self.code:
                errors["code"] = "SKU编号创建后不可修改；停用后也不复用。"
            previous_product_id = SKU.objects.filter(pk=self.pk).values_list("product_id", flat=True).first()
            if previous_product_id != self.product_id:
                errors["product"] = "SKU创建后不能改挂到其他产品。"
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.code} · {self.product} · {self.unit}"


class Warehouse(models.Model):
    name = models.CharField("仓库名称", max_length=255)
    address = models.TextField("地址")
    is_active = models.BooleanField("启用", default=True)

    class Meta:
        ordering = ("id",)

    def clean(self):
        self.name = self.name.strip()
        self.address = self.address.strip()
        if not self.name:
            raise ValidationError({"name": "仓库名称不能为空。"})
        if not self.address:
            raise ValidationError({"address": "仓库地址不能为空。"})

    def __str__(self):
        return self.name


class Supplier(models.Model):
    name = models.CharField("供应商名称", max_length=255, unique=True)
    is_active = models.BooleanField("启用", default=True)

    class Meta:
        ordering = ("id",)

    def clean(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValidationError({"name": "供应商名称不能为空。"})

    def __str__(self):
        return self.name


class BusinessControl(models.Model):
    last_sequence = models.PositiveBigIntegerField(default=0)
    low_stock_enabled = models.BooleanField(default=True)
    default_threshold = models.PositiveBigIntegerField(default=5)
    opening_completed = models.BooleanField(default=False)

    class Meta:
        permissions = (("manage_global_low_stock", "管理全局低库存提醒"),)


class InventoryBalance(models.Model):
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="inventory_balances")
    sku = models.ForeignKey(SKU, on_delete=models.PROTECT, related_name="inventory_balances")
    on_hand = models.BigIntegerField(default=0)
    occupied = models.BigIntegerField(default=0)
    reserved = models.BigIntegerField(default=0)
    hoarded = models.BigIntegerField(default=0)
    damaged = models.BigIntegerField(default=0)
    cost_amount = models.DecimalField(max_digits=20, decimal_places=3, default=0)
    version = models.PositiveIntegerField(default=1)
    quantity_version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ("warehouse_id", "sku_id")
        constraints = [
            models.UniqueConstraint(fields=("warehouse", "sku"), name="crm_balance_warehouse_sku_unique"),
            models.CheckConstraint(
                condition=Q(on_hand__gte=0, occupied__gte=0, reserved__gte=0, hoarded__gte=0, damaged__gte=0),
                name="crm_balance_quantities_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(occupied__lte=models.F("on_hand") - models.F("reserved") - models.F("hoarded") - models.F("damaged")),
                name="crm_balance_restricted_within_stock",
            ),
            models.CheckConstraint(
                condition=~Q(on_hand=0) | Q(cost_amount=0),
                name="crm_balance_empty_cost_zero",
            ),
            models.CheckConstraint(condition=Q(cost_amount__gte=0), name="crm_balance_cost_nonnegative"),
        ]


class Receipt(models.Model):
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="receipts")
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="receipts")
    sequence = models.PositiveBigIntegerField(unique=True)
    version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="receipts_created")
    confirmed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-sequence",)
        permissions = (("confirm_receipt", "确认采购入库"),)


class OpeningBatch(models.Model):
    sequence = models.PositiveBigIntegerField(unique=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="opening_batches_created")
    confirmed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-sequence",)
        permissions = (("confirm_opening", "确认期初盘点"),)


class ReceiptLine(models.Model):
    receipt = models.ForeignKey(Receipt, on_delete=models.PROTECT, related_name="lines")
    line_number = models.PositiveIntegerField()
    sku = models.ForeignKey(SKU, on_delete=models.PROTECT, related_name="receipt_lines")
    quantity = models.PositiveBigIntegerField()
    purchase_price = models.DecimalField(max_digits=20, decimal_places=3)
    inbound_freight = models.DecimalField(max_digits=20, decimal_places=3, default=0)
    cost_amount = models.DecimalField(max_digits=20, decimal_places=3)
    reserved_quantity = models.PositiveBigIntegerField(default=0)
    damaged_quantity = models.PositiveBigIntegerField(default=0)
    low_stock_threshold = models.PositiveBigIntegerField(null=True, blank=True)

    class Meta:
        ordering = ("line_number",)
        constraints = [
            models.UniqueConstraint(fields=("receipt", "line_number"), name="crm_receipt_line_number_unique"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="crm_receipt_line_positive_quantity"),
            models.CheckConstraint(
                condition=Q(purchase_price__gte=0, inbound_freight__gte=0),
                name="crm_receipt_line_costs_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(reserved_quantity__lte=models.F("quantity") - models.F("damaged_quantity")),
                name="crm_receipt_line_restrictions_fit",
            ),
        ]


class OpeningLine(models.Model):
    batch = models.ForeignKey(OpeningBatch, on_delete=models.PROTECT, related_name="lines")
    line_number = models.PositiveIntegerField()
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="opening_lines")
    sku = models.ForeignKey(SKU, on_delete=models.PROTECT, related_name="opening_lines")
    quantity = models.PositiveBigIntegerField()
    unit_cost = models.DecimalField(max_digits=20, decimal_places=3)
    cost_amount = models.DecimalField(max_digits=20, decimal_places=3)
    reserved_quantity = models.PositiveBigIntegerField(default=0)
    hoarded_quantity = models.PositiveBigIntegerField(default=0)
    hoard_reason = models.TextField(blank=True)
    damaged_quantity = models.PositiveBigIntegerField(default=0)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ("line_number",)
        constraints = [
            models.UniqueConstraint(fields=("batch", "line_number"), name="crm_opening_line_number_unique"),
            models.CheckConstraint(
                condition=Q(reserved_quantity__lte=models.F("quantity") - models.F("hoarded_quantity") - models.F("damaged_quantity")),
                name="crm_opening_line_restrictions_fit",
            ),
            models.CheckConstraint(condition=Q(unit_cost__gte=0), name="crm_opening_line_cost_nonnegative"),
        ]


class InventoryHold(models.Model):
    class Type(models.TextChoices):
        CUSTOMER_RESERVATION = "customer_reservation", "客户预留"
        HOARD = "hoard", "囤货"

    balance = models.ForeignKey(InventoryBalance, on_delete=models.PROTECT, related_name="holds")
    receipt_line = models.ForeignKey(ReceiptLine, null=True, blank=True, on_delete=models.PROTECT, related_name="holds")
    opening_line = models.ForeignKey(OpeningLine, null=True, blank=True, on_delete=models.PROTECT, related_name="holds")
    type = models.CharField(max_length=24, choices=Type.choices)
    initial_quantity = models.PositiveBigIntegerField()
    remaining_quantity = models.PositiveBigIntegerField()
    reason = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="inventory_holds_created")
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-id",)
        permissions = (
            ("create_inventory_hold", "创建预留或囤货"),
            ("release_inventory_hold", "释放指定预留或囤货"),
        )
        constraints = [
            models.CheckConstraint(condition=Q(initial_quantity__gt=0), name="crm_hold_positive_initial"),
            models.CheckConstraint(condition=Q(remaining_quantity__lte=models.F("initial_quantity")), name="crm_hold_remaining_within_initial"),
            models.CheckConstraint(
                condition=Q(receipt_line__isnull=True) | Q(opening_line__isnull=True),
                name="crm_hold_at_most_one_source_line",
            ),
            models.CheckConstraint(
                condition=~Q(type="hoard") | Q(reason__gt=""),
                name="crm_hoard_reason_required",
            ),
        ]


class DamagedRecord(models.Model):
    balance = models.ForeignKey(InventoryBalance, on_delete=models.PROTECT, related_name="damaged_records")
    receipt_line = models.ForeignKey(ReceiptLine, null=True, blank=True, on_delete=models.PROTECT, related_name="damaged_records")
    opening_line = models.ForeignKey(OpeningLine, null=True, blank=True, on_delete=models.PROTECT, related_name="damaged_records")
    original_quantity = models.PositiveBigIntegerField()
    remaining_quantity = models.PositiveBigIntegerField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="damaged_records_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-id",)
        constraints = [
            models.CheckConstraint(condition=Q(original_quantity__gt=0), name="crm_damage_positive_original"),
            models.CheckConstraint(
                condition=(Q(receipt_line__isnull=False, opening_line__isnull=True) | Q(receipt_line__isnull=True, opening_line__isnull=False)),
                name="crm_damage_one_source_line",
            ),
            models.CheckConstraint(
                condition=Q(remaining_quantity__lte=models.F("original_quantity")),
                name="crm_damage_remaining_within_original",
            ),
        ]


class BusinessOperation(models.Model):
    operation_key = models.UUIDField(unique=True)
    sequence = models.PositiveBigIntegerField(unique=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="business_operations")
    action = models.CharField(max_length=40)
    input_hash = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    schema_version = models.PositiveSmallIntegerField(default=1)
    result = models.JSONField(default=dict)
    receipt = models.ForeignKey(Receipt, null=True, blank=True, on_delete=models.PROTECT, related_name="operations")
    opening_batch = models.ForeignKey(OpeningBatch, null=True, blank=True, on_delete=models.PROTECT, related_name="operations")
    hold = models.ForeignKey(InventoryHold, null=True, blank=True, on_delete=models.PROTECT, related_name="operations")
    control = models.ForeignKey(BusinessControl, null=True, blank=True, on_delete=models.PROTECT, related_name="operations")
    sku = models.ForeignKey(SKU, null=True, blank=True, on_delete=models.PROTECT, related_name="low_stock_operations")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("sequence",)
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(receipt__isnull=False, opening_batch__isnull=True, hold__isnull=True, control__isnull=True, sku__isnull=True)
                    | Q(receipt__isnull=True, opening_batch__isnull=False, hold__isnull=True, control__isnull=True, sku__isnull=True)
                    | Q(receipt__isnull=True, opening_batch__isnull=True, hold__isnull=False, control__isnull=True, sku__isnull=True)
                    | Q(receipt__isnull=True, opening_batch__isnull=True, hold__isnull=True, control__isnull=False, sku__isnull=True)
                    | Q(receipt__isnull=True, opening_batch__isnull=True, hold__isnull=True, control__isnull=True, sku__isnull=False)
                ),
                name="crm_operation_one_target",
            )
        ]


class InventoryMovement(models.Model):
    operation = models.ForeignKey(BusinessOperation, on_delete=models.PROTECT, related_name="movements")
    balance = models.ForeignKey(InventoryBalance, on_delete=models.PROTECT, related_name="movements")
    receipt_line = models.ForeignKey(ReceiptLine, null=True, blank=True, on_delete=models.PROTECT, related_name="movements")
    opening_line = models.ForeignKey(OpeningLine, null=True, blank=True, on_delete=models.PROTECT, related_name="movements")
    hold = models.ForeignKey(InventoryHold, null=True, blank=True, on_delete=models.PROTECT, related_name="movements")
    event_kind = models.CharField(max_length=24)
    entry_key = models.CharField(max_length=80)
    on_hand_delta = models.BigIntegerField(default=0)
    occupied_delta = models.BigIntegerField(default=0)
    reserved_delta = models.BigIntegerField(default=0)
    hoarded_delta = models.BigIntegerField(default=0)
    damaged_delta = models.BigIntegerField(default=0)
    cost_delta = models.DecimalField(max_digits=20, decimal_places=3, default=0)
    cost_revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("operation__sequence", "id")
        constraints = [
            models.UniqueConstraint(fields=("operation", "balance", "event_kind", "entry_key"), name="crm_movement_operation_balance_entry_unique"),
            models.CheckConstraint(
                condition=(
                    Q(receipt_line__isnull=False, opening_line__isnull=True, hold__isnull=True)
                    | Q(receipt_line__isnull=True, opening_line__isnull=False, hold__isnull=True)
                    | Q(receipt_line__isnull=True, opening_line__isnull=True, hold__isnull=False)
                ),
                name="crm_movement_one_source",
            ),
        ]


def receipt_image_upload_to(instance, filename):
    from uuid import uuid4

    return f"receipt-images/{uuid4().hex}.image"


class ReceiptImage(models.Model):
    file = models.FileField(upload_to=receipt_image_upload_to)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="receipt_images")
    receipt = models.ForeignKey(Receipt, null=True, blank=True, on_delete=models.PROTECT, related_name="images")
    image_format = models.CharField(max_length=8)
    size = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("id",)


class PermissionChange(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="修改人",
        on_delete=models.PROTECT,
        related_name="permission_changes_made",
    )
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="员工",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="permission_changes_received",
    )
    role_name = models.CharField("固定角色", max_length=32, blank=True)
    before = models.JSONField("修改前")
    after = models.JSONField("修改后")
    changed_at = models.DateTimeField("修改时间", auto_now_add=True)

    class Meta:
        ordering = ("-changed_at", "-id")
        constraints = [
            models.CheckConstraint(
                condition=(Q(target_user__isnull=False, role_name="") | Q(target_user__isnull=True, role_name__in=["销售", "仓管／财务", "老板"])),
                name="crm_permission_change_one_target",
            )
        ]

    def __str__(self):
        target = self.target_user or self.role_name
        return f"{target} · {self.changed_at:%Y-%m-%d %H:%M}"
