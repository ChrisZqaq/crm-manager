from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower


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


class ProductLanguageOption(models.Model):
    name = models.CharField("语言版本", max_length=80, unique=True)

    class Meta:
        ordering = ("id",)
        verbose_name = "商品语言"
        verbose_name_plural = "商品语言"

    def __str__(self):
        return self.name

    def clean(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValidationError({"name": "语言名称不能为空。"})


class Product(models.Model):
    class Type(models.TextChoices):
        BOOSTER = "booster", "补充包"
        MERCHANDISE = "merchandise", "周边"
        GIFT_SET = "gift_set", "礼盒·预组"

    brand = models.ForeignKey(Brand, verbose_name="IP", on_delete=models.PROTECT, related_name="products")
    type = models.CharField("商品类型", max_length=16, choices=Type.choices)
    series = models.CharField("系列", max_length=120, blank=True)
    name = models.CharField("产品名", max_length=255)
    language = models.ForeignKey(
        ProductLanguageOption, verbose_name="语言版本", null=True, blank=True,
        on_delete=models.PROTECT, related_name="products",
    )
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
                condition=~Q(type__in=["booster", "gift_set"]) | Q(language__isnull=False),
                name="crm_product_language_required",
            ),
        ]

    def clean(self):
        self.series = self.series.strip()
        self.name = self.name.strip()
        self.official_code = self.official_code.strip()
        self.aliases = [value.strip() for value in self.aliases if value.strip()]
        self.aliases = list(dict.fromkeys(self.aliases))

        errors = {}
        if not self.name:
            errors["name"] = "产品名不能为空。"
        if self.type in (self.Type.BOOSTER, self.Type.GIFT_SET) and not self.language_id:
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
            parts.append(self.language.name)
        return " · ".join(parts)


class SKU(models.Model):
    class PackType(models.TextChoices):
        SLIM = "slim", "瘦包"
        FAT = "fat", "肥包"
        EMPTY = "empty", "空"

    product = models.ForeignKey(Product, verbose_name="产品", on_delete=models.PROTECT, related_name="skus")
    code = models.CharField("内部SKU编号", max_length=255, unique=True)
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
            previous = SKU.objects.filter(pk=self.pk).values("code", "product_id", "unit").first()
            if previous and previous["code"] != self.code:
                errors["code"] = "SKU编号创建后不可修改；停用后也不复用。"
            if previous and previous["product_id"] != self.product_id:
                errors["product"] = "SKU创建后不能改挂到其他产品。"
            if previous and previous["unit"] != self.unit and (
                InventoryBalance.objects.filter(sku_id=self.pk).exists()
                or InventoryMovement.objects.filter(balance__sku_id=self.pk).exists()
                or ReceiptLine.objects.filter(sku_id=self.pk).exists()
                or OpeningLine.objects.filter(sku_id=self.pk).exists()
            ):
                errors["unit"] = "已有库存业务或余额记录，不能修改售卖单位。"
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
    last_sales_order_date = models.DateField(null=True, blank=True)
    last_sales_order_daily_sequence = models.PositiveBigIntegerField(default=0)
    low_stock_enabled = models.BooleanField(default=True)
    default_threshold = models.PositiveBigIntegerField(default=5)
    opening_completed = models.BooleanField(default=False)
    opening_enabled = models.BooleanField(default=True)

    class Meta:
        permissions = (
            ("manage_global_low_stock", "管理全局低库存提醒"),
            ("manage_opening", "管理正式期初入口"),
        )


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
    version = models.PositiveIntegerField(default=1)
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
              models.CheckConstraint(condition=Q(initial_quantity__gte=0), name="crm_hold_initial_nonnegative"),
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
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-id",)
        constraints = [
              models.CheckConstraint(condition=Q(original_quantity__gte=0), name="crm_damage_nonnegative_original"),
            models.CheckConstraint(
                condition=(Q(receipt_line__isnull=False, opening_line__isnull=True) | Q(receipt_line__isnull=True, opening_line__isnull=False)),
                name="crm_damage_one_source_line",
            ),
              models.CheckConstraint(
                  condition=Q(remaining_quantity__lte=models.F("original_quantity")),
                  name="crm_damage_remaining_within_original",
              ),
          ]


class SplitRecord(models.Model):
    source_warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="split_sources")
    source_sku = models.ForeignKey(SKU, on_delete=models.PROTECT, related_name="split_sources")
    source_quantity = models.PositiveBigIntegerField()
    target_warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="split_targets")
    target_sku = models.ForeignKey(SKU, on_delete=models.PROTECT, related_name="split_targets")
    target_quantity = models.PositiveBigIntegerField()
    transferred_cost = models.DecimalField(max_digits=20, decimal_places=3, default=0)
    reason = models.TextField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="inventory_splits_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-id",)
        permissions = (("create_inventory_split", "登记拆箱转换"),)
        constraints = [
            models.CheckConstraint(condition=Q(source_quantity__gt=0, target_quantity__gt=0), name="crm_split_quantities_positive"),
            models.CheckConstraint(condition=~Q(source_sku=F("target_sku")), name="crm_split_sku_must_change"),
            models.CheckConstraint(condition=Q(transferred_cost__gte=0), name="crm_split_cost_nonnegative"),
        ]


class GiftRecord(models.Model):
    reason = models.TextField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="inventory_gifts_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-id",)
        permissions = (("create_inventory_gift", "登记赠送出库"),)


class GiftLine(models.Model):
    gift = models.ForeignKey(GiftRecord, on_delete=models.PROTECT, related_name="lines")
    line_number = models.PositiveIntegerField()
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="gift_lines")
    sku = models.ForeignKey(SKU, on_delete=models.PROTECT, related_name="gift_lines")
    quantity = models.PositiveBigIntegerField()
    cost_amount = models.DecimalField(max_digits=20, decimal_places=3, default=0)

    class Meta:
        ordering = ("line_number",)
        constraints = [
            models.UniqueConstraint(fields=("gift", "line_number"), name="crm_gift_line_number_unique"),
            models.UniqueConstraint(fields=("gift", "warehouse", "sku"), name="crm_gift_key_unique"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="crm_gift_line_quantity_positive"),
            models.CheckConstraint(condition=Q(cost_amount__gte=0), name="crm_gift_line_cost_nonnegative"),
        ]


class DamageResolution(models.Model):
    class Kind(models.TextChoices):
        EXCHANGE = "exchange", "换货"
        REFUND = "refund", "退款"

    damaged_record = models.ForeignKey(DamagedRecord, on_delete=models.PROTECT, related_name="resolutions")
    kind = models.CharField(max_length=8, choices=Kind.choices)
    quantity = models.PositiveBigIntegerField()
    refund_amount = models.DecimalField(max_digits=20, decimal_places=3, null=True, blank=True)
    deducted_cost = models.DecimalField(max_digits=20, decimal_places=3, default=0)
    difference = models.DecimalField(max_digits=20, decimal_places=3, default=0)
    unreturned_freight = models.DecimalField(max_digits=20, decimal_places=3, default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="damage_resolutions_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-id",)
        permissions = (("resolve_damaged_record", "处理破损库存"),)
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="crm_damage_resolution_quantity_positive"),
            models.CheckConstraint(condition=Q(kind__in=("exchange", "refund")), name="crm_damage_resolution_kind_valid"),
            models.CheckConstraint(condition=Q(deducted_cost__gte=0, unreturned_freight__gte=0), name="crm_damage_resolution_costs_nonnegative"),
            models.CheckConstraint(
                condition=(Q(kind="exchange", refund_amount__isnull=True, difference=0, unreturned_freight=0)
                           | Q(kind="refund", refund_amount__gte=0, difference=F("deducted_cost") - F("refund_amount"))),
                name="crm_damage_resolution_amounts_valid",
            ),
        ]


class BusinessOperation(models.Model):
    operation_key = models.UUIDField(unique=True)
    sequence = models.PositiveBigIntegerField(unique=True, null=True, blank=True)
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
    customer = models.ForeignKey("Customer", null=True, blank=True, on_delete=models.PROTECT, related_name="operations")
    sales_order = models.ForeignKey("SalesOrder", null=True, blank=True, on_delete=models.PROTECT, related_name="operations")
    account_profile = models.ForeignKey("AccountProfile", null=True, blank=True, on_delete=models.PROTECT, related_name="operations")
    split_record = models.ForeignKey(SplitRecord, null=True, blank=True, on_delete=models.PROTECT, related_name="operations")
    gift = models.ForeignKey(GiftRecord, null=True, blank=True, on_delete=models.PROTECT, related_name="operations")
    damage_resolution = models.ForeignKey(DamageResolution, null=True, blank=True, on_delete=models.PROTECT, related_name="operations")
    stock_count = models.ForeignKey("StockCount", null=True, blank=True, on_delete=models.PROTECT, related_name="operations")
    cost_correction = models.ForeignKey("CostCorrection", null=True, blank=True, on_delete=models.PROTECT, related_name="operations")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("sequence",)
        constraints = [
            models.CheckConstraint(
                condition=(
                        Q(receipt__isnull=False, opening_batch__isnull=True, hold__isnull=True, control__isnull=True, sku__isnull=True, customer__isnull=True, sales_order__isnull=True, account_profile__isnull=True, split_record__isnull=True, gift__isnull=True, damage_resolution__isnull=True, stock_count__isnull=True, cost_correction__isnull=True)
                        | Q(receipt__isnull=True, opening_batch__isnull=False, hold__isnull=True, control__isnull=True, sku__isnull=True, customer__isnull=True, sales_order__isnull=True, account_profile__isnull=True, split_record__isnull=True, gift__isnull=True, damage_resolution__isnull=True, stock_count__isnull=True, cost_correction__isnull=True)
                        | Q(receipt__isnull=True, opening_batch__isnull=True, hold__isnull=False, control__isnull=True, sku__isnull=True, customer__isnull=True, sales_order__isnull=True, account_profile__isnull=True, split_record__isnull=True, gift__isnull=True, damage_resolution__isnull=True, stock_count__isnull=True, cost_correction__isnull=True)
                        | Q(receipt__isnull=True, opening_batch__isnull=True, hold__isnull=True, control__isnull=False, sku__isnull=True, customer__isnull=True, sales_order__isnull=True, account_profile__isnull=True, split_record__isnull=True, gift__isnull=True, damage_resolution__isnull=True, stock_count__isnull=True, cost_correction__isnull=True)
                        | Q(receipt__isnull=True, opening_batch__isnull=True, hold__isnull=True, control__isnull=True, sku__isnull=False, customer__isnull=True, sales_order__isnull=True, account_profile__isnull=True, split_record__isnull=True, gift__isnull=True, damage_resolution__isnull=True, stock_count__isnull=True, cost_correction__isnull=True)
                        | Q(receipt__isnull=True, opening_batch__isnull=True, hold__isnull=True, control__isnull=True, sku__isnull=True, customer__isnull=False, sales_order__isnull=True, account_profile__isnull=True, split_record__isnull=True, gift__isnull=True, damage_resolution__isnull=True, stock_count__isnull=True, cost_correction__isnull=True)
                        | Q(receipt__isnull=True, opening_batch__isnull=True, hold__isnull=True, control__isnull=True, sku__isnull=True, customer__isnull=True, sales_order__isnull=False, account_profile__isnull=True, split_record__isnull=True, gift__isnull=True, damage_resolution__isnull=True, stock_count__isnull=True, cost_correction__isnull=True)
                        | Q(receipt__isnull=True, opening_batch__isnull=True, hold__isnull=True, control__isnull=True, sku__isnull=True, customer__isnull=True, sales_order__isnull=True, account_profile__isnull=False, split_record__isnull=True, gift__isnull=True, damage_resolution__isnull=True, stock_count__isnull=True, cost_correction__isnull=True)
                        | Q(receipt__isnull=True, opening_batch__isnull=True, hold__isnull=True, control__isnull=True, sku__isnull=True, customer__isnull=True, sales_order__isnull=True, account_profile__isnull=True, split_record__isnull=False, gift__isnull=True, damage_resolution__isnull=True, stock_count__isnull=True, cost_correction__isnull=True)
                        | Q(receipt__isnull=True, opening_batch__isnull=True, hold__isnull=True, control__isnull=True, sku__isnull=True, customer__isnull=True, sales_order__isnull=True, account_profile__isnull=True, split_record__isnull=True, gift__isnull=False, damage_resolution__isnull=True, stock_count__isnull=True, cost_correction__isnull=True)
                        | Q(receipt__isnull=True, opening_batch__isnull=True, hold__isnull=True, control__isnull=True, sku__isnull=True, customer__isnull=True, sales_order__isnull=True, account_profile__isnull=True, split_record__isnull=True, gift__isnull=True, damage_resolution__isnull=False, stock_count__isnull=True, cost_correction__isnull=True)
                        | Q(receipt__isnull=True, opening_batch__isnull=True, hold__isnull=True, control__isnull=True, sku__isnull=True, customer__isnull=True, sales_order__isnull=True, account_profile__isnull=True, split_record__isnull=True, gift__isnull=True, damage_resolution__isnull=True, stock_count__isnull=False, cost_correction__isnull=True)
                        | Q(receipt__isnull=True, opening_batch__isnull=True, hold__isnull=True, control__isnull=True, sku__isnull=True, customer__isnull=True, sales_order__isnull=True, account_profile__isnull=True, split_record__isnull=True, gift__isnull=True, damage_resolution__isnull=True, stock_count__isnull=True, cost_correction__isnull=False)
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
    sales_order = models.ForeignKey("SalesOrder", null=True, blank=True, on_delete=models.PROTECT, related_name="inventory_movements")
    split_record = models.ForeignKey(SplitRecord, null=True, blank=True, on_delete=models.PROTECT, related_name="movements")
    gift_line = models.ForeignKey(GiftLine, null=True, blank=True, on_delete=models.PROTECT, related_name="movements")
    damage_resolution = models.ForeignKey(DamageResolution, null=True, blank=True, on_delete=models.PROTECT, related_name="movements")
    stock_count_line = models.ForeignKey("StockCountLine", null=True, blank=True, on_delete=models.PROTECT, related_name="movements")
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
                        Q(receipt_line__isnull=False, opening_line__isnull=True, hold__isnull=True, sales_order__isnull=True, split_record__isnull=True, gift_line__isnull=True, damage_resolution__isnull=True, stock_count_line__isnull=True)
                        | Q(receipt_line__isnull=True, opening_line__isnull=False, hold__isnull=True, sales_order__isnull=True, split_record__isnull=True, gift_line__isnull=True, damage_resolution__isnull=True, stock_count_line__isnull=True)
                        | Q(receipt_line__isnull=True, opening_line__isnull=True, hold__isnull=False, sales_order__isnull=True, split_record__isnull=True, gift_line__isnull=True, damage_resolution__isnull=True, stock_count_line__isnull=True)
                        | Q(receipt_line__isnull=True, opening_line__isnull=True, hold__isnull=True, sales_order__isnull=False, split_record__isnull=True, gift_line__isnull=True, damage_resolution__isnull=True, stock_count_line__isnull=True)
                        | Q(receipt_line__isnull=True, opening_line__isnull=True, hold__isnull=True, sales_order__isnull=True, split_record__isnull=False, gift_line__isnull=True, damage_resolution__isnull=True, stock_count_line__isnull=True)
                        | Q(receipt_line__isnull=True, opening_line__isnull=True, hold__isnull=True, sales_order__isnull=True, split_record__isnull=True, gift_line__isnull=False, damage_resolution__isnull=True, stock_count_line__isnull=True)
                        | Q(receipt_line__isnull=True, opening_line__isnull=True, hold__isnull=True, sales_order__isnull=True, split_record__isnull=True, gift_line__isnull=True, damage_resolution__isnull=False, stock_count_line__isnull=True)
                        | Q(receipt_line__isnull=True, opening_line__isnull=True, hold__isnull=True, sales_order__isnull=True, split_record__isnull=True, gift_line__isnull=True, damage_resolution__isnull=True, stock_count_line__isnull=False)
                ),
                name="crm_movement_one_source",
            ),
          ]


class Customer(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="crm_customers")
    name = models.CharField(max_length=200)
    contact = models.TextField(blank=True)
    company = models.CharField(max_length=200, blank=True)
    aliases = models.JSONField(default=list, blank=True, validators=[validate_aliases])
    note1 = models.TextField("备注1", blank=True)
    note2 = models.TextField("备注2", blank=True)
    invoice_title = models.CharField("发票抬头", max_length=255, blank=True)
    invoice_credit_code = models.CharField("统一社会信用代码", max_length=100, blank=True)
    invoice_email = models.CharField("发票邮箱", max_length=254, blank=True)
    is_active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("id",)
        permissions = (
            ("view_all_customers", "查看全部客户"),
            ("change_all_customers", "维护全部客户"),
            ("archive_customer", "归档客户"),
            ("change_customer_owner", "转交客户"),
        )

    def __str__(self):
        return self.name


class CustomerAddress(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="addresses")
    recipient = models.CharField(max_length=120)
    contact = models.TextField()
    province = models.CharField(max_length=80)
    city = models.CharField(max_length=80)
    detail = models.TextField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("id",)


class Channel(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("id",)

    def __str__(self):
        return self.name


class AccountProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="crm_profile")
    wecom_userid = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        validators=[RegexValidator(
            regex=r"\A[A-Za-z0-9][A-Za-z0-9_.@-]{0,63}\Z",
            message="企微成员ID须为1至64字节，以字母或数字开头，且只含字母、数字、下划线、减号、@或点。",
            code="invalid_wecom_userid",
        )],
    )
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                Lower("wecom_userid"),
                condition=Q(wecom_userid__isnull=False),
                name="crm_profile_wecomid_ci",
            ),
        ]


class PaymentAccount(models.Model):
    class Method(models.TextChoices):
        BANK = "bank", "银行"
        WECHAT = "wechat", "微信"
        ALIPAY = "alipay", "支付宝"

    class Responsibility(models.TextChoices):
        PUBLIC = "public", "对公／财务核对"
        OWNER_PRIVATE = "owner_private", "老板私账／老板核对"

    name = models.CharField(max_length=255)
    method = models.CharField(max_length=16, choices=Method.choices)
    responsibility = models.CharField(max_length=24, choices=Responsibility.choices)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("name", "id")
        constraints = [
            models.CheckConstraint(condition=Q(method__in=("bank", "wechat", "alipay")), name="crm_payment_account_method"),
            models.CheckConstraint(condition=Q(responsibility__in=("public", "owner_private")), name="crm_payment_account_responsibility"),
        ]

    def __str__(self):
        return self.name


class SalesOrder(models.Model):
    class Status(models.TextChoices):
        AWAITING_PAYMENT = "awaiting_payment", "待付款凭证"
        READY_TO_SHIP = "ready_to_ship", "待出库"
        SHIPPED = "shipped", "待收货"
        COMPLETED = "completed", "已完成"
        CANCELLED = "cancelled", "已取消"

    class DeliveryMode(models.TextChoices):
        SHIPMENT = "shipment", "快递"
        ON_SITE = "on_site", "现场"

    class CostBasis(models.TextChoices):
        ORDER_SNAPSHOT = "order_snapshot", "下单成本快照"
        ACTUAL_OUTFLOW = "actual_outflow", "出库实际成本"

    salesperson = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sales_orders")
    order_number = models.CharField(max_length=27, null=True, blank=True, unique=True, editable=False)
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.PROTECT, related_name="sales_orders")
    channel = models.ForeignKey(Channel, on_delete=models.PROTECT, related_name="sales_orders")
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.AWAITING_PAYMENT)
    delivery_mode = models.CharField(max_length=16, choices=DeliveryMode.choices)
    cost_basis = models.CharField(max_length=20, choices=CostBasis.choices, default=CostBasis.ORDER_SNAPSHOT)
    customer_name_snapshot = models.CharField(max_length=200, blank=True)
    recipient_snapshot = models.CharField(max_length=120, blank=True)
    contact_snapshot = models.TextField(blank=True)
    province_snapshot = models.CharField(max_length=80, blank=True)
    city_snapshot = models.CharField(max_length=80, blank=True)
    detail_snapshot = models.TextField(blank=True)
    requires_invoice = models.BooleanField(default=False)
    invoice_title = models.CharField(max_length=255, blank=True)
    invoice_credit_code = models.CharField(max_length=100, blank=True)
    invoice_email = models.CharField(max_length=254, blank=True)
    remarks = models.TextField(blank=True)
    revenue_amount = models.DecimalField(max_digits=20, decimal_places=3, default=0)
    cost_amount = models.DecimalField(max_digits=20, decimal_places=3, null=True, blank=True, default=0)
    margin_amount = models.DecimalField(max_digits=20, decimal_places=3, null=True, blank=True, default=0)
    commission_estimate = models.DecimalField(max_digits=20, decimal_places=3, null=True, blank=True, default=0)
    version = models.PositiveIntegerField(default=1)
    payment_version = models.PositiveIntegerField(default=1)
    amount_version = models.PositiveIntegerField(default=1)
    last_sequence = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-id",)
        permissions = (
            ("view_all_sales_orders", "查看全部销售订单"),
            ("change_all_sales_orders", "维护全部销售订单"),
            ("cancel_salesorder", "取消未交付销售订单"),
            ("register_payment", "登记订单收款凭证"),
            ("ship_salesorder", "确认订单发货"),
            ("deliver_on_site", "确认现场交付"),
            ("confirm_salesorder_receipt", "确认订单收货"),
        )
        constraints = [
            models.CheckConstraint(condition=Q(status__in=("awaiting_payment", "ready_to_ship", "shipped", "completed", "cancelled")), name="crm_order_phase4_status"),
            models.CheckConstraint(condition=Q(delivery_mode__in=("shipment", "on_site")), name="crm_order_delivery_mode"),
              models.CheckConstraint(condition=Q(cost_basis__in=("order_snapshot", "actual_outflow")), name="crm_order_cost_basis_valid"),
            models.CheckConstraint(condition=Q(revenue_amount__gte=0, cost_amount__gte=0), name="crm_order_totals_nonnegative"),
        ]


class SalesOrderLine(models.Model):
    order = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, related_name="lines")
    line_number = models.PositiveIntegerField()
    sku = models.ForeignKey(SKU, on_delete=models.PROTECT, related_name="sales_order_lines")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="sales_order_lines")
    quantity = models.PositiveBigIntegerField()
    unit_price = models.DecimalField(max_digits=20, decimal_places=3)
    revenue_amount = models.DecimalField(max_digits=20, decimal_places=3)
    unit_cost_snapshot = models.DecimalField(max_digits=20, decimal_places=3, null=True, blank=True)
    order_cost_amount = models.DecimalField(max_digits=20, decimal_places=3, null=True, blank=True)
    outbound_freight = models.DecimalField(max_digits=20, decimal_places=3, default=0)
    cost_mode = models.CharField(max_length=24, default="average_snapshot")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ("line_number", "id")
        constraints = [
            models.UniqueConstraint(fields=("order", "line_number"), name="crm_order_line_number_unique"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="crm_order_line_positive_quantity"),
            models.CheckConstraint(
                condition=Q(unit_price__gte=0, revenue_amount__gte=0, unit_cost_snapshot__gte=0, order_cost_amount__gte=0, outbound_freight__gte=0),
                name="crm_order_line_amounts_nonnegative",
            ),
        ]


class PaymentRecord(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "待核对"
        PROBLEM = "problem", "待处理"
        PASSED = "passed", "核对通过"

    order = models.OneToOneField(SalesOrder, on_delete=models.PROTECT, related_name="payment_record")
    account = models.ForeignKey(PaymentAccount, on_delete=models.PROTECT, related_name="payment_records")
    registered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="registered_payments")
    expected_amount = models.DecimalField(max_digits=20, decimal_places=3)
    received_amount = models.DecimalField(max_digits=20, decimal_places=3)
    expected_payment_version = models.PositiveIntegerField(default=1)
    verified_version = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    paused = models.BooleanField(default=False)
    difference_reason = models.TextField(blank=True)
    problem_reason = models.TextField(blank=True)
    first_evidence_registered_at = models.DateTimeField(null=True, blank=True, editable=False)
    received_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="verified_payments")
    verified_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        permissions = (("verify_payment", "核对订单收款"),)
        constraints = [
            models.CheckConstraint(condition=Q(status__in=("pending", "problem", "passed")), name="crm_payment_status_valid"),
            models.CheckConstraint(condition=Q(expected_amount__gte=0, received_amount__gte=0), name="crm_payment_amounts_nonnegative"),
            models.CheckConstraint(condition=~Q(status="problem") | Q(problem_reason__gt=""), name="crm_payment_problem_has_reason"),
        ]


class Shipment(models.Model):
    class Carrier(models.TextChoices):
        CROSSING = "crossing", "跨越"
        SF = "sf", "顺丰"
        HUOLALA = "huolala", "货拉拉"

    order = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, related_name="shipments")
    carrier = models.CharField(max_length=16, choices=Carrier.choices)
    tracking_number = models.CharField(max_length=255)
    warehouses = models.ManyToManyField(Warehouse, through="ShipmentWarehouse", related_name="shipments")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="shipments_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("id",)
        constraints = [models.CheckConstraint(condition=Q(carrier__in=("crossing", "sf", "huolala")), name="crm_shipment_carrier_valid")]


class ShipmentWarehouse(models.Model):
    shipment = models.ForeignKey(Shipment, on_delete=models.PROTECT, related_name="warehouse_links")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="shipment_links")

    class Meta:
        constraints = [models.UniqueConstraint(fields=("shipment", "warehouse"), name="crm_shipment_warehouse_unique")]


class CommissionReview(models.Model):
    order = models.OneToOneField(SalesOrder, on_delete=models.PROTECT, related_name="commission_review")
    approved_amount = models.DecimalField(max_digits=20, decimal_places=3, null=True, blank=True)
    approved_amount_version = models.PositiveIntegerField(null=True, blank=True)
    first_earned_at = models.DateTimeField(null=True, blank=True)
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="commission_reviews")
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        permissions = (("review_commission", "复核订单提成"), ("view_all_commissions", "查看全部订单提成"))
        constraints = [
            models.CheckConstraint(
                condition=Q(approved_amount__isnull=True, approved_amount_version__isnull=True, first_earned_at__isnull=True)
                | Q(approved_amount__isnull=False, approved_amount_version__isnull=False, first_earned_at__isnull=False),
                name="crm_commission_review_snapshot_complete",
            ),
        ]


class CommissionAdjustment(models.Model):
    class Kind(models.TextChoices):
        INITIAL = "initial", "首次核定"
        REREVIEW = "rereview", "重审差额"
        COST_CORRECTION = "cost_correction", "成本纠错差额"

    approval_operation = models.ForeignKey("BusinessOperation", on_delete=models.PROTECT, related_name="commission_adjustments")
    order = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, related_name="commission_adjustments")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    old_amount = models.DecimalField(max_digits=20, decimal_places=3)
    new_amount = models.DecimalField(max_digits=20, decimal_places=3)
    delta = models.DecimalField(max_digits=20, decimal_places=3)
    confirmed_at = models.DateTimeField()
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="completed_commission_adjustments")
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("confirmed_at", "id")
        permissions = (("complete_commission_adjustment", "确认本人提成差额已线下处理"),)
        constraints = [
            models.UniqueConstraint(fields=("approval_operation", "order"), name="crm_commission_adjustment_operation_order_unique"),
            models.CheckConstraint(condition=Q(delta=models.F("new_amount") - models.F("old_amount")), name="crm_commission_adjustment_delta_valid"),
              models.CheckConstraint(condition=Q(kind__in=("initial", "rereview", "cost_correction")), name="crm_commission_adjustment_kind_valid"),
            models.CheckConstraint(
                condition=Q(completed_by__isnull=True, completed_at__isnull=True)
                | Q(completed_by__isnull=False, completed_at__isnull=False),
                  name="crm_commission_adjustment_completion_pair",
              ),
          ]


class StockCount(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "待审批"
        APPROVED = "approved", "已批准"

    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    reason = models.TextField()
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="stock_counts_submitted")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="stock_counts_approved")
    submitted_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ("-id",)
        permissions = (("approve_stockcount", "审批库存盘点调整"),)
        constraints = [
            models.CheckConstraint(condition=Q(status__in=("pending", "approved")), name="crm_stock_count_status_valid"),
            models.CheckConstraint(
                condition=Q(status="pending", approved_by__isnull=True, approved_at__isnull=True)
                | Q(status="approved", approved_by__isnull=False, approved_at__isnull=False),
                name="crm_stock_count_approval_pair",
            ),
        ]


class StockCountLine(models.Model):
    class CostMode(models.TextChoices):
        AVERAGE = "average", "移动平均"
        MANUAL = "manual", "人工输入"

    stock_count = models.ForeignKey(StockCount, on_delete=models.PROTECT, related_name="lines")
    balance = models.ForeignKey(InventoryBalance, on_delete=models.PROTECT, related_name="stock_count_lines")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="stock_count_lines")
    sku = models.ForeignKey(SKU, on_delete=models.PROTECT, related_name="stock_count_lines")
    observed_quantity = models.BigIntegerField()
    captured_quantity_version = models.PositiveIntegerField()
    basis_cost_version = models.PositiveIntegerField()
    delta = models.BigIntegerField(default=0)
    cost_mode = models.CharField(max_length=8, choices=CostMode.choices)
    manual_unit_cost = models.DecimalField(max_digits=20, decimal_places=3, null=True, blank=True)
    supplemental_unit_cost = models.DecimalField(max_digits=20, decimal_places=3, null=True, blank=True)
    supplement_approval = models.ForeignKey("CostCorrection", null=True, blank=True, on_delete=models.PROTECT, related_name="supplemented_count_lines")
    effective_unit_cost = models.DecimalField(max_digits=20, decimal_places=3, default=0)
    cost_delta = models.DecimalField(max_digits=20, decimal_places=3, default=0)

    class Meta:
        ordering = ("stock_count_id", "warehouse_id", "sku_id")
        constraints = [
            models.UniqueConstraint(fields=("stock_count", "warehouse", "sku"), name="crm_stock_count_key_unique"),
            models.CheckConstraint(condition=Q(observed_quantity__gte=0), name="crm_stock_count_observed_nonnegative"),
            models.CheckConstraint(condition=Q(cost_mode__in=("average", "manual")), name="crm_stock_count_cost_mode_valid"),
            models.CheckConstraint(
                condition=(Q(cost_mode="average", manual_unit_cost__isnull=True)
                           | Q(cost_mode="manual", manual_unit_cost__gte=0)),
                name="crm_stock_count_manual_cost_valid",
            ),
            models.CheckConstraint(
                condition=Q(supplemental_unit_cost__isnull=True) | Q(supplemental_unit_cost__gte=0),
                name="crm_stock_count_supplemental_cost_nonnegative",
            ),
            models.CheckConstraint(condition=Q(effective_unit_cost__gte=0), name="crm_stock_count_effective_cost_nonnegative"),
        ]


class CostCorrection(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "待审批"
        APPLIED = "applied", "已生效"
        REJECTED = "rejected", "已拒绝"

    receipt = models.ForeignKey(Receipt, null=True, blank=True, on_delete=models.PROTECT, related_name="cost_corrections")
    opening_batch = models.ForeignKey(OpeningBatch, null=True, blank=True, on_delete=models.PROTECT, related_name="cost_corrections")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    reason = models.TextField()
    original_input = models.JSONField(default=dict)
    proposed_input = models.JSONField(default=dict)
    supplemental_count_costs = models.JSONField(default=list)
    before_after = models.JSONField(default=dict)
    preview_sequence = models.PositiveBigIntegerField()
    submitted_sequence = models.PositiveBigIntegerField(default=0)
    request_version = models.PositiveIntegerField()
    submitter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="cost_corrections_submitted")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="cost_corrections_reviewed")
    submitted_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ("-id",)
        permissions = (
            ("submit_costcorrection", "提交采购或期初纠错"),
            ("approve_costcorrection", "审批采购或期初纠错"),
            ("reject_costcorrection", "拒绝采购或期初纠错"),
        )
        constraints = [
            models.CheckConstraint(
                condition=(Q(receipt__isnull=False, opening_batch__isnull=True)
                           | Q(receipt__isnull=True, opening_batch__isnull=False)),
                name="crm_cost_correction_one_source",
            ),
            models.CheckConstraint(condition=Q(status__in=("pending", "applied", "rejected")), name="crm_cost_correction_status_valid"),
            models.CheckConstraint(
                condition=Q(status="pending", reviewer__isnull=True, reviewed_at__isnull=True)
                | Q(status__in=("applied", "rejected"), reviewer__isnull=False, reviewed_at__isnull=False),
                name="crm_cost_correction_review_pair",
            ),
        ]


def receipt_image_upload_to(instance, filename):
    from uuid import uuid4

    return f"receipt-images/{uuid4().hex}.image"


class ReceiptImage(models.Model):
    file = models.FileField(upload_to=receipt_image_upload_to)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="receipt_images")
    receipt = models.ForeignKey(Receipt, null=True, blank=True, on_delete=models.PROTECT, related_name="images")
    payment = models.ForeignKey(PaymentRecord, null=True, blank=True, on_delete=models.PROTECT, related_name="images")
    image_format = models.CharField(max_length=8)
    size = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("id",)
        constraints = [
            models.CheckConstraint(condition=Q(receipt__isnull=True) | Q(payment__isnull=True), name="crm_image_single_document"),
        ]


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
