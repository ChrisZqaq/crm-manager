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
    is_active = models.BooleanField("启用", default=True)

    class Meta:
        ordering = ("id",)

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
