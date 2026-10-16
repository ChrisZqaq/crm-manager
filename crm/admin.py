from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group
from django.db import transaction

from .forms import (
    AdministratorChangeForm,
    EmployeeAddForm,
    EmployeeChangeForm,
    ProductAdminForm,
    RoleGroupAdminForm,
    SKUAdminForm,
    WarehouseAdminForm,
)
from .models import Brand, BusinessControl, Channel, PaymentAccount, PermissionChange, Product, ProductLanguageOption, SKU, Supplier, Warehouse
from .permissions import ROLE_NAMES, ROLE_OWNER


class NoAdminHistory:
    change_list_template = "crm/admin_change_list.html"
    change_form_template = "crm/admin_change_form.html"
    class Media:
        css = {"all": ("crm/admin.css",)}

    field_labels = {
        "name": "名称", "sku_prefix": "SKU前缀", "type": "商品类型", "series": "系列",
        "language": "语言", "official_code": "官方编号", "code": "SKU编号", "unit": "售卖单位",
        "barcode": "包装条码", "pack_type": "包装类型",
        "address": "地址",
        "is_active": "启用", "is_staff": "可登录后台", "is_superuser": "超级管理员",
        "groups": "角色", "permissions": "权限", "changed_at": "变更时间",
        "actor": "操作人", "target_user": "员工", "role_name": "角色名称",
        "before": "变更前", "after": "变更后", "method": "收款方式",
        "responsibility": "归属", "email": "邮箱", "username": "账号",
        "first_name": "名", "last_name": "姓",
    }

    def log_addition(self, request, obj, message):
        pass

    def log_change(self, request, obj, message):
        pass

    def log_deletion(self, request, obj, object_repr):
        pass

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        field = super().formfield_for_dbfield(db_field, request, **kwargs)
        if field and db_field.name in self.field_labels:
            field.label = self.field_labels[db_field.name]
        return field


class ArchiveAdmin(NoAdminHistory, admin.ModelAdmin):
    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        if request.method == "POST":
            with transaction.atomic():
                BusinessControl.objects.select_for_update().get(pk=1)
                return super().changeform_view(request, object_id, form_url, extra_context)
        return super().changeform_view(request, object_id, form_url, extra_context)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Brand)
class BrandAdmin(ArchiveAdmin):
    list_display = ("name", "sku_prefix", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "sku_prefix")


@admin.register(Product)
class ProductAdmin(ArchiveAdmin):
    form = ProductAdminForm
    list_display = ("name", "brand", "type", "series", "language", "is_active")
    list_filter = ("brand", "type", "is_active")
    search_fields = ("name", "official_code", "series", "brand__name")


@admin.register(ProductLanguageOption)
class ProductLanguageOptionAdmin(ArchiveAdmin):
    list_display = ("name",)
    search_fields = ("name",)

    def _is_owner(self, request):
        return request.user.is_superuser or request.user.groups.filter(name=ROLE_OWNER).exists()

    def has_module_permission(self, request):
        return self._is_owner(request)

    def has_view_permission(self, request, obj=None):
        return self._is_owner(request)

    def has_add_permission(self, request):
        return self._is_owner(request)

    def has_change_permission(self, request, obj=None):
        return self._is_owner(request)

    def has_delete_permission(self, request, obj=None):
        return self._is_owner(request)


@admin.register(SKU)
class SKUAdmin(ArchiveAdmin):
    form = SKUAdminForm
    list_display = ("code", "product", "unit", "barcode", "pack_type", "is_active")
    list_filter = ("product__brand", "product__type", "pack_type", "is_active")
    search_fields = ("code", "barcode", "product__name", "product__brand__name")

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj:
            fields.append("code")
        return fields


@admin.register(Warehouse)
class WarehouseAdmin(ArchiveAdmin):
    form = WarehouseAdminForm
    list_display = ("name", "address", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "address")


@admin.register(Supplier)
class SupplierAdmin(ArchiveAdmin):
    list_display = ("name", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(PaymentAccount)
class PaymentAccountAdmin(ArchiveAdmin):
    list_display = ("name", "method", "responsibility", "is_active")
    list_filter = ("method", "responsibility", "is_active")
    search_fields = ("name",)


User = get_user_model()
admin.site.unregister(User)
admin.site.unregister(Group)


@admin.register(User)
class EmployeeAdmin(NoAdminHistory, UserAdmin):
    add_form = EmployeeAddForm
    form = EmployeeChangeForm
    ordering = ("username",)
    list_display = ("username", "first_name", "last_name", "is_active", "role_names")
    list_filter = ("is_active", "groups")
    search_fields = ("username", "first_name", "last_name", "email")

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if not request.user.is_superuser:
            queryset = queryset.filter(is_superuser=False)
        return queryset

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            kwargs["form"] = self.add_form
        else:
            kwargs["form"] = AdministratorChangeForm if request.user.is_superuser else self.form
        return super().get_form(request, obj, **kwargs)

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return ((None, {"fields": (
                "username", "password1", "password2", "first_name", "last_name", "email", "groups",
            )}),)
        fields = ["username", "first_name", "last_name", "email", "is_active", "groups"]
        if request.user.is_superuser:
            fields.extend(("is_staff", "is_superuser"))
        return (("员工", {"fields": tuple(fields)}),)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name == "groups":
            kwargs["queryset"] = Group.objects.filter(name__in=ROLE_NAMES).order_by("name")
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def has_add_permission(self, request):
        return request.user.is_superuser or request.user.has_perm("auth.add_user")

    def has_change_permission(self, request, obj=None):
        if not (request.user.is_superuser or request.user.has_perm("auth.change_user")):
            return False
        return obj is None or request.user.is_superuser or not obj.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        form._permission_snapshot_before = self._user_permission_snapshot(obj) if change else None
        if not obj.is_superuser:
            obj.is_staff = True
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        before = form._permission_snapshot_before
        after = self._user_permission_snapshot(form.instance)
        if before != after:
            PermissionChange.objects.create(
                actor=request.user,
                target_user=form.instance,
                before=before or {"groups": [], "is_active": None},
                after=after,
            )

    @staticmethod
    def _user_permission_snapshot(user):
        stored_user = User.objects.get(pk=user.pk)
        return {
            "groups": list(stored_user.groups.order_by("name").values_list("name", flat=True)),
            "is_active": stored_user.is_active,
            "is_staff": stored_user.is_staff,
            "is_superuser": stored_user.is_superuser,
        }

    @admin.display(description="普通角色")
    def role_names(self, user):
        return ", ".join(user.groups.order_by("name").values_list("name", flat=True))


@admin.register(Group)
class RoleGroupAdmin(NoAdminHistory, admin.ModelAdmin):
    form = RoleGroupAdminForm
    list_display = ("name",)
    ordering = ("name",)
    fields = ("name", "permissions")
    readonly_fields = ("name",)

    def get_queryset(self, request):
        return super().get_queryset(request).filter(name__in=ROLE_NAMES)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        if not (request.user.is_superuser or request.user.has_perm("auth.change_group")):
            return False
        return obj is None or obj.name in ROLE_NAMES

    def save_model(self, request, obj, form, change):
        form._permission_snapshot_before = self._role_permission_snapshot(obj) if change else []
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        before = form._permission_snapshot_before
        after = self._role_permission_snapshot(form.instance)
        if before != after:
            PermissionChange.objects.create(
                actor=request.user,
                role_name=form.instance.name,
                before={"permissions": before},
                after={"permissions": after},
            )

    @staticmethod
    def _role_permission_snapshot(group):
        return list(
            group.permissions.order_by("content_type__app_label", "codename")
            .values_list("content_type__app_label", "codename")
        )


@admin.register(PermissionChange)
class PermissionChangeAdmin(NoAdminHistory, admin.ModelAdmin):
    list_display = ("changed_at", "actor", "target_user", "role_name")
    list_filter = ("changed_at",)
    readonly_fields = ("actor", "target_user", "role_name", "before", "after", "changed_at")
    fields = readonly_fields
    ordering = ("-changed_at", "-id")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Channel)
class ChannelAdmin(ArchiveAdmin):
    list_display = ("name", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)


admin.site.index_template = "crm/admin_index.html"
admin.site.site_header = "阿罗拉潮玩空间 CRM 管理后台"
admin.site.site_title = "CRM 管理后台"
admin.site.index_title = "系统管理"
