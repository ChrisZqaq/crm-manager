from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError

from .models import Product, SKU, Warehouse
from .permissions import MANAGEABLE_PERMISSIONS, ROLE_NAMES


def parse_optional_bool(value):
    if value in (True, "true"):
        return True
    if value in (False, "false"):
        return False
    return None


class AliasFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound and self.instance.pk:
            self.initial["aliases"] = "\n".join(self.instance.aliases)

    def clean_aliases(self):
        values = [value.strip() for value in (self.cleaned_data["aliases"] or "").splitlines()]
        return list(dict.fromkeys(value for value in values if value))


class SeriesTextInput(forms.TextInput):
    template_name = "crm/widgets/series_text.html"

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context["widget"]["series_options"] = self.series_options
        return context


class OptionalBooleanChoiceField(forms.TypedChoiceField):
    def prepare_value(self, value):
        if value is True:
            return "true"
        if value is False:
            return "false"
        return value


class ProductAdminForm(AliasFormMixin, forms.ModelForm):
    series = forms.CharField(
        label="系列",
        required=False,
        widget=SeriesTextInput(attrs={"list": "id_product_series_options"}),
    )
    aliases = forms.CharField(
        label="别名",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="每行填写一个别名，可留空。",
    )
    contains_cards = OptionalBooleanChoiceField(
        label="是否含卡牌",
        choices=(("", "请选择"), ("true", "含卡牌"), ("false", "不含卡牌")),
        coerce=parse_optional_bool,
        empty_value=None,
        required=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["series"].widget.series_options = list(
            Product.objects.exclude(series="").order_by("series").values_list("series", flat=True).distinct()
        )

    class Meta:
        model = Product
        fields = (
            "brand",
            "type",
            "contains_cards",
            "series",
            "name",
            "language",
            "official_code",
            "aliases",
            "is_active",
        )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("type") == Product.Type.BOOSTER:
            if cleaned.get("contains_cards") is False:
                self.add_error("contains_cards", "补充包固定为含卡牌。")
            else:
                cleaned["contains_cards"] = True
                self.instance.contains_cards = True
        elif cleaned.get("contains_cards") is None:
            self.add_error("contains_cards", "请明确选择是否含卡牌。")
        return cleaned


class SKUAdminForm(AliasFormMixin, forms.ModelForm):
    aliases = forms.CharField(
        label="别名",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="每行填写一个别名，可留空。",
    )
    confirm_duplicate_barcode = forms.BooleanField(
        label="我已核对重复条码候选，仍确认保存",
        required=False,
    )

    class Meta:
        model = SKU
        fields = (
            "product",
            "code",
            "box_spec",
            "pack_spec",
            "pack_type",
            "unit",
            "barcode",
            "aliases",
            "is_active",
        )

    def clean(self):
        cleaned = super().clean()
        barcode = (cleaned.get("barcode") or "").strip()
        original = self.initial.get("barcode", "")
        if barcode and barcode != original:
            candidates = (
                SKU.objects.filter(barcode=barcode)
                .exclude(pk=self.instance.pk)
                .select_related("product__brand")
                .order_by("id")
            )
            if candidates.exists() and not cleaned.get("confirm_duplicate_barcode"):
                descriptions = "; ".join(
                    f"{item.product.brand.name} / {item.product.name}"
                    f" {item.product.language} / {item.code}（{item.unit}）"
                    for item in candidates
                )
                self.add_error("barcode", f"该条码已有SKU：{descriptions}。请核对并勾选确认后重试。")
        return cleaned


class WarehouseAdminForm(forms.ModelForm):
    confirm_duplicate_name = forms.BooleanField(
        label="我已核对同名仓库候选，仍确认保存",
        required=False,
    )

    class Meta:
        model = Warehouse
        fields = ("name", "address", "is_active")

    def clean(self):
        cleaned = super().clean()
        name = (cleaned.get("name") or "").strip()
        original = self.initial.get("name", "")
        if name and name.casefold() != original.casefold():
            matches = Warehouse.objects.filter(name__iexact=name).exclude(pk=self.instance.pk).order_by("id")
            if matches.exists() and not cleaned.get("confirm_duplicate_name"):
                candidates = "; ".join(f"{item.name}（{item.address}）" for item in matches)
                self.add_error("name", f"已存在同名仓库：{candidates}。请核对并勾选确认后重试。")
        return cleaned


User = get_user_model()


def role_group_queryset():
    return Group.objects.filter(name__in=ROLE_NAMES).order_by("name")


class EmployeeAddForm(UserCreationForm):
    first_name = forms.CharField(label="名字", required=False, max_length=150)
    last_name = forms.CharField(label="姓氏", required=False, max_length=150)
    email = forms.EmailField(label="邮箱", required=False)
    groups = forms.ModelMultipleChoiceField(label="普通角色", queryset=role_group_queryset(), required=False)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name", "email", "groups")

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_staff = True
        user.is_active = True
        if commit:
            user.save()
            self.save_m2m()
        return user


class EmployeeChangeForm(forms.ModelForm):
    groups = forms.ModelMultipleChoiceField(label="普通角色", queryset=role_group_queryset(), required=False)

    class Meta:
        model = User
        fields = ("username", "first_name", "last_name", "email", "is_active", "groups")


class AdministratorChangeForm(forms.ModelForm):
    groups = forms.ModelMultipleChoiceField(label="普通角色", queryset=role_group_queryset(), required=False)

    class Meta:
        model = User
        fields = (
            "username",
            "first_name",
            "last_name",
            "email",
            "is_active",
            "is_staff",
            "is_superuser",
            "groups",
        )


class RoleGroupAdminForm(forms.ModelForm):
    permissions = forms.ModelMultipleChoiceField(
        label="角色权限开关",
        queryset=Permission.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"size": 20}),
        help_text="只列出本系统已定义的基础资料及员工/角色操作权限。",
    )
    name = forms.CharField(label="固定角色", disabled=True)

    class Meta:
        model = Group
        fields = ("name", "permissions")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        allowed = [
            (app_label, codename)
            for app_label, codename in MANAGEABLE_PERMISSIONS
        ]
        self.fields["permissions"].queryset = Permission.objects.filter(
            content_type__app_label__in={app for app, _ in allowed},
            codename__in={code for _, code in allowed},
        ).select_related("content_type").order_by("content_type__app_label", "codename")

    def clean_name(self):
        name = self.cleaned_data["name"]
        if name not in ROLE_NAMES:
            raise ValidationError("只能维护系统预设的三个普通角色。")
        return name
