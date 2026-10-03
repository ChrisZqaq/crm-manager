from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase

from .catalog import search_products, search_skus, search_warehouses
from .forms import ProductAdminForm, SKUAdminForm, WarehouseAdminForm
from .models import Brand, PermissionChange, Product, SKU, Warehouse
from .permissions import ROLE_DEFAULTS, ROLE_OWNER, ROLE_SALES, ROLE_WAREHOUSE


User = get_user_model()


class CatalogTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.brand = Brand.objects.create(name="星海", sku_prefix="SEA")
        cls.product = Product.objects.create(
            brand=cls.brand,
            type=Product.Type.BOOSTER,
            contains_cards=True,
            series="第一弹",
            name="潮汐",
            language="简体中文",
            aliases=["潮汐盒"],
        )
        cls.sku = SKU.objects.create(
            product=cls.product,
            code="SEA0001",
            unit="盒",
            barcode="000123456789",
            aliases=["整盒"],
        )

    def make_user(self, role=None, **kwargs):
        user = User.objects.create_user(username=f"user-{User.objects.count() + 1}", password="correct horse battery")
        if role:
            user.groups.add(Group.objects.get(name=role))
        return user

    def test_fixed_roles_have_expected_defaults_and_permissions_are_union(self):
        for role, expected in ROLE_DEFAULTS.items():
            group = Group.objects.get(name=role)
            actual = set(group.permissions.values_list("content_type__app_label", "codename"))
            self.assertEqual(actual, set(expected))
            self.assertFalse(any(codename.startswith("delete_") for _, codename in actual))

        user = self.make_user()
        sales = Group.objects.get(name=ROLE_SALES)
        warehouse = Group.objects.get(name=ROLE_WAREHOUSE)
        permissions = Permission.objects.filter(
            content_type__app_label="crm",
            codename__in=("view_brand", "view_product", "view_sku"),
        )
        sales.permissions.set(permissions.filter(codename__in=("view_brand", "view_product")))
        warehouse.permissions.set(permissions.filter(codename="view_sku"))
        user.groups.add(sales, warehouse)
        self.assertTrue(user.has_perms(("crm.view_brand", "crm.view_product", "crm.view_sku")))

    def test_api_and_html_access_require_login_and_permissions(self):
        api = self.client.get("/api/v1/skus")
        self.assertEqual(api.status_code, 401)
        self.assertEqual(api.json()["error"]["code"], "authentication_required")
        self.assertIn("request_id", api.json())
        self.assertEqual(self.client.get("/catalog/").status_code, 302)
        self.assertEqual(self.client.get("/accounts/register/").status_code, 404)

        user = self.make_user()
        self.client.force_login(user)
        denied = self.client.get("/api/v1/skus")
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()["error"]["code"], "permission_denied")
        self.assertEqual(self.client.get("/catalog/").status_code, 403)

    def test_csrf_protects_password_login(self):
        user = self.make_user(ROLE_SALES)
        client = Client(enforce_csrf_checks=True)
        response = client.get("/accounts/login/")
        self.assertEqual(response.status_code, 200)
        token = client.cookies["csrftoken"].value
        login_data = {"username": user.username, "password": "correct horse battery"}
        self.assertEqual(client.post("/accounts/login/", login_data).status_code, 403)
        self.assertEqual(
            client.post("/accounts/login/", login_data, HTTP_X_CSRFTOKEN=token).status_code,
            302,
        )

    def test_role_permission_change_takes_effect_on_the_next_request(self):
        user = self.make_user(ROLE_SALES)
        self.client.force_login(user)
        self.assertEqual(self.client.get("/api/v1/products").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/skus").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/warehouses").status_code, 200)

        sales = Group.objects.get(name=ROLE_SALES)
        sales.permissions.remove(
            Permission.objects.get(content_type__app_label="crm", codename="view_sku")
        )
        denied = self.client.get("/api/v1/skus")
        self.assertEqual(denied.status_code, 403)

    def test_search_prioritizes_exact_matches_and_excludes_archived_records(self):
        partial = Product.objects.create(
            brand=self.brand,
            type=Product.Type.MERCHANDISE,
            contains_cards=False,
            name="潮汐盒纪念品",
        )
        inactive = Product.objects.create(
            brand=self.brand,
            type=Product.Type.MERCHANDISE,
            contains_cards=False,
            name="潮汐盒停用商品",
            is_active=False,
        )
        products = search_products({"q": "潮汐盒"})
        self.assertEqual([item["id"] for item in products], [self.product.pk, partial.pk])
        self.assertNotIn(inactive.pk, [item["id"] for item in products])
        self.assertEqual([item["id"] for item in search_skus({"q": "000123456789"})], [self.sku.pk])
        SKU.objects.create(product=self.product, code="SEA0002", unit="盒", is_active=False)
        self.assertEqual(search_products({"q": "SEA0002"}), [])
        self.assertEqual(
            [item["code"] for item in search_products({"q": "潮汐盒"})[0]["skus"]],
            [self.sku.code],
        )

        warehouse = Warehouse.objects.create(name="市区仓", address="上海市测试路 1 号")
        archived = Warehouse.objects.create(name="旧仓", address="上海市测试路 2 号", is_active=False)
        self.assertEqual([item["id"] for item in search_warehouses({"q": "市区"})], [warehouse.pk])
        self.assertNotIn(archived.pk, [item["id"] for item in search_warehouses({"q": "旧仓"})])

    def test_invalid_filters_and_pagination_are_rejected(self):
        self.client.force_login(self.make_user(ROLE_SALES))
        response = self.client.get("/api/v1/products?type=unknown")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_filter")
        response = self.client.get("/api/v1/products?page_size=201")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_pagination")

    def test_product_cards_and_language_rules_are_validated(self):
        missing_choice = Product(
            brand=self.brand,
            type=Product.Type.MERCHANDISE,
            name="未确定卡牌",
        )
        with self.assertRaises(ValidationError):
            missing_choice.full_clean()

        card_product = Product(
            brand=self.brand,
            type=Product.Type.GIFT_SET,
            contains_cards=True,
            name="语言缺失礼盒",
        )
        with self.assertRaises(ValidationError):
            card_product.full_clean()

        invalid_booster = Product(
            brand=self.brand,
            type=Product.Type.BOOSTER,
            contains_cards=False,
            name="错误补充包",
            language="简体中文",
        )
        with self.assertRaises(ValidationError):
            invalid_booster.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.create(
                brand=self.brand,
                type=Product.Type.BOOSTER,
                contains_cards=False,
                name="数据库错误补充包",
                language="简体中文",
            )

    def test_product_form_shows_existing_cards_choice(self):
        merchandise = Product.objects.create(
            brand=self.brand,
            type=Product.Type.MERCHANDISE,
            contains_cards=False,
            name="钥匙扣",
        )
        form = ProductAdminForm(instance=merchandise)
        self.assertIn('value="false" selected', form["contains_cards"].as_widget())

    def test_sku_numbers_are_stable_and_barcode_duplicates_need_confirmation(self):
        changed = SKU.objects.get(pk=self.sku.pk)
        changed.code = "SEA0002"
        with self.assertRaises(ValidationError):
            changed.full_clean()

        wrong_prefix = SKU(product=self.product, code="OTHER0001", unit="盒")
        with self.assertRaises(ValidationError):
            wrong_prefix.full_clean()
        valid_leading_zeroes = SKU(product=self.product, code="SEA0002", unit="盒")
        valid_leading_zeroes.full_clean()
        SKU(product=self.product, code="SEA0000", unit="盒").full_clean()

        data = {
            "product": self.product.pk,
            "code": "SEA0002",
            "box_spec": "",
            "pack_spec": "",
            "pack_type": "",
            "unit": "盒",
            "barcode": self.sku.barcode,
            "aliases": "",
            "is_active": "on",
        }
        form = SKUAdminForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn("SEA0001", form.errors["barcode"][0])
        data["confirm_duplicate_barcode"] = "on"
        form = SKUAdminForm(data=data)
        self.assertTrue(form.is_valid(), form.errors)

        product = Product.objects.get(pk=self.product.pk)
        product.series = "已更新系列"
        product.save()
        brand = Brand.objects.get(pk=self.brand.pk)
        brand.sku_prefix = "NEW"
        with self.assertRaises(ValidationError):
            brand.full_clean()

    def test_duplicate_warehouse_name_requires_confirmation(self):
        Warehouse.objects.create(name="上海仓", address="地址一")
        data = {"name": "上海仓", "address": "地址二", "is_active": "on"}
        form = WarehouseAdminForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertTrue(form.errors.get("name"))
        data["confirm_duplicate_name"] = "on"
        form = WarehouseAdminForm(data=data)
        self.assertTrue(form.is_valid(), form.errors)


class EmployeeAdminTestCase(TestCase):
    def test_owner_cannot_view_or_create_superuser_but_admin_can_grant_and_revoke(self):
        owner_role = Group.objects.get(name=ROLE_OWNER)
        owner = User.objects.create_user(
            username="owner", password="owner password long enough", is_staff=True
        )
        owner.groups.add(owner_role)
        administrator = User.objects.create_superuser(
            username="administrator", email="admin@example.test", password="administrator password"
        )
        target = User.objects.create_user(username="target", password="target password long enough")
        self.client.force_login(owner)

        add_page = self.client.get("/admin/auth/user/add/")
        self.assertEqual(add_page.status_code, 200)
        self.assertNotContains(add_page, 'name="is_superuser"')
        response = self.client.get(f"/admin/auth/user/{administrator.pk}/change/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/admin/")
        self.assertNotIn(
            f"/admin/auth/user/{administrator.pk}/change/",
            self.client.get("/admin/auth/user/").content.decode(),
        )

        self.client.force_login(administrator)
        change_url = f"/admin/auth/user/{target.pk}/change/"
        change_page = self.client.get(change_url)
        self.assertEqual(change_page.status_code, 200)
        self.assertContains(change_page, 'name="is_superuser"')
        base_data = {
            "username": target.username,
            "first_name": "",
            "last_name": "",
            "email": "",
            "is_active": "on",
            "is_staff": "on",
            "_save": "保存",
        }
        response = self.client.post(change_url, {**base_data, "is_superuser": "on"})
        self.assertEqual(response.status_code, 302)
        target.refresh_from_db()
        self.assertTrue(target.is_superuser)

        response = self.client.post(change_url, base_data)
        self.assertEqual(response.status_code, 302)
        target.refresh_from_db()
        self.assertFalse(target.is_superuser)
        snapshots = {
            (entry.before["is_superuser"], entry.after["is_superuser"])
            for entry in PermissionChange.objects.filter(target_user=target)
        }
        self.assertIn((False, True), snapshots)
        self.assertIn((True, False), snapshots)

    def test_employee_role_changes_are_audited(self):
        owner = User.objects.create_user(username="owner", password="owner password long enough", is_staff=True)
        owner.groups.add(Group.objects.get(name=ROLE_OWNER))
        employee = User.objects.create_user(username="employee", password="employee password long enough")
        employee.groups.add(Group.objects.get(name=ROLE_SALES))
        self.client.force_login(owner)
        response = self.client.post(
            f"/admin/auth/user/{employee.pk}/change/",
            {
                "username": employee.username,
                "first_name": "",
                "last_name": "",
                "email": "",
                "is_active": "on",
                "groups": str(Group.objects.get(name=ROLE_WAREHOUSE).pk),
                "_save": "保存",
            },
        )
        self.assertEqual(response.status_code, 302)
        entry = PermissionChange.objects.get(target_user=employee)
        self.assertEqual(entry.actor, owner)
        self.assertEqual(entry.before["groups"], [ROLE_SALES])
        self.assertEqual(entry.after["groups"], [ROLE_WAREHOUSE])

    def test_role_permission_changes_are_audited(self):
        owner = User.objects.create_user(username="owner", password="owner password long enough", is_staff=True)
        owner.groups.add(Group.objects.get(name=ROLE_OWNER))
        sales = Group.objects.get(name=ROLE_SALES)
        permissions = list(sales.permissions.exclude(codename="view_sku").values_list("pk", flat=True))
        self.client.force_login(owner)
        response = self.client.post(
            f"/admin/auth/group/{sales.pk}/change/",
            {"permissions": [str(permission) for permission in permissions], "_save": "保存"},
        )
        self.assertEqual(response.status_code, 302)
        entry = PermissionChange.objects.get(role_name=ROLE_SALES)
        self.assertIn(["crm", "view_sku"], entry.before["permissions"])
        self.assertNotIn(["crm", "view_sku"], entry.after["permissions"])
