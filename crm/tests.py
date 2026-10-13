import json
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase

from .catalog import search_products, search_skus, search_warehouses
from .forms import ProductAdminForm, SKUAdminForm, WarehouseAdminForm
from .models import Brand, BusinessOperation, InventoryBalance, PermissionChange, Product, ProductLanguageOption, SKU, Warehouse
from .permissions import (
    PRE_D14_WAREHOUSE_DEFAULTS,
    ROLE_DEFAULTS,
    ROLE_OWNER,
    ROLE_SALES,
    ROLE_WAREHOUSE,
    create_fixed_role_groups,
)


User = get_user_model()


class CatalogTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.brand = Brand.objects.create(name="星海", sku_prefix="SEA")
        language, _ = ProductLanguageOption.objects.get_or_create(name="简中")
        cls.product = Product.objects.create(
            brand=cls.brand,
            type=Product.Type.BOOSTER,
            series="第一弹",
            name="潮汐",
            language=language,
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

    def test_warehouse_customer_permission_upgrade_preserves_custom_permissions(self):
        self.assertIn(("crm", "add_customer"), ROLE_DEFAULTS[ROLE_WAREHOUSE])
        self.assertNotIn(("crm", "view_customer"), ROLE_DEFAULTS[ROLE_WAREHOUSE])
        self.assertNotIn(("crm", "view_all_customers"), ROLE_DEFAULTS[ROLE_WAREHOUSE])
        self.assertNotIn(("crm", "change_all_customers"), ROLE_DEFAULTS[ROLE_WAREHOUSE])

        warehouse = Group.objects.get(name=ROLE_WAREHOUSE)
        previous = [
            Permission.objects.get(content_type__app_label=app_label, codename=codename)
            for app_label, codename in PRE_D14_WAREHOUSE_DEFAULTS
        ]
        warehouse.permissions.set(previous)
        create_fixed_role_groups(sender=None)
        upgraded = set(warehouse.permissions.values_list("content_type__app_label", "codename"))
        self.assertEqual(upgraded, set(ROLE_DEFAULTS[ROLE_WAREHOUSE]))

        custom = Permission.objects.get(content_type__app_label="crm", codename="view_sku")
        warehouse.permissions.set((custom,))
        create_fixed_role_groups(sender=None)
        self.assertEqual(
            set(warehouse.permissions.values_list("content_type__app_label", "codename")),
            {("crm", "view_sku")},
        )

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

    def test_workbench_and_orders_expose_order_creation_by_permission(self):
        sales = self.make_user(ROLE_SALES)
        self.client.force_login(sales)
        workbench = self.client.get("/")
        orders = self.client.get("/orders/")
        for response in (workbench, orders):
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "新建订单")
            self.assertContains(response, 'href="/orders/new/"')
        self.assertEqual(self.client.get("/orders/new/").status_code, 200)
        catalog = self.client.get("/catalog/")
        self.assertEqual(catalog.status_code, 200)
        for label, href in (("销售订单", "/orders/"), ("客户", "/customers/"), ("库存", "/inventory/")):
            self.assertContains(catalog, label)
            self.assertContains(catalog, f'href="{href}"')

        catalog_only = self.make_user()
        catalog_only.user_permissions.set(Permission.objects.filter(
            content_type__app_label="crm",
            codename__in=("view_brand", "view_product", "view_sku"),
        ))
        self.client.force_login(catalog_only)
        catalog = self.client.get("/catalog/")
        workbench = self.client.get("/")
        self.assertEqual(catalog.status_code, 200)
        self.assertEqual(workbench.status_code, 200)
        for response in (catalog, workbench):
            for href in ("/orders/new/", "/orders/", "/customers/", "/inventory/"):
                self.assertNotContains(response, f'href="{href}"')
        self.assertEqual(self.client.get("/orders/new/").status_code, 403)

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

    def test_password_login_defaults_to_workbench_and_preserves_safe_next(self):
        user = self.make_user(ROLE_SALES)
        anonymous = Client().get("/")
        self.assertEqual(anonymous.status_code, 302)

        for login_url, redirect_to in (
            ("/accounts/login/", "/"),
            ("/accounts/login/?next=/catalog/", "/catalog/"),
        ):
            client = Client(enforce_csrf_checks=True)
            self.assertEqual(client.get(login_url).status_code, 200)
            token = client.cookies["csrftoken"].value
            response = client.post(
                login_url,
                {"username": user.username, "password": "correct horse battery"},
                HTTP_X_CSRFTOKEN=token,
            )
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response["Location"], redirect_to)
            self.assertEqual(client.get(redirect_to).status_code, 200)

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
            name="潮汐盒纪念品",
        )
        inactive = Product.objects.create(
            brand=self.brand,
            type=Product.Type.MERCHANDISE,
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

    def test_product_types_drive_language_validation(self):
        merchandise = Product(
            brand=self.brand,
            type=Product.Type.MERCHANDISE,
            name="钥匙扣",
        )
        merchandise.full_clean()

        for product_type in (Product.Type.BOOSTER, Product.Type.GIFT_SET):
            with self.subTest(product_type=product_type):
                product = Product(brand=self.brand, type=product_type, name=f"缺少语言-{product_type}")
                with self.assertRaises(ValidationError) as raised:
                    product.full_clean()
                self.assertIn("language", raised.exception.message_dict)

        merchandise = Product.objects.create(
            brand=self.brand,
            type=Product.Type.MERCHANDISE,
            name="可改类型周边",
        )
        changed_data = {
            "brand": self.brand.pk,
            "type": Product.Type.GIFT_SET,
            "series": "",
            "name": merchandise.name,
            "language": "",
            "official_code": "",
            "aliases": "",
            "is_active": "on",
        }
        form = ProductAdminForm(data=changed_data, instance=merchandise)
        self.assertFalse(form.is_valid())
        self.assertIn("language", form.errors)

        for product_type in (Product.Type.BOOSTER, Product.Type.GIFT_SET):
            with self.subTest(database_product_type=product_type):
                with transaction.atomic():
                    with self.assertRaises(IntegrityError):
                        Product.objects.create(
                            brand=self.brand,
                            type=product_type,
                            name=f"数据库缺少语言-{product_type}",
                        )

        valid_merchandise = Product.objects.create(
            brand=self.brand,
            type=Product.Type.MERCHANDISE,
            name="数据库周边",
        )
        self.assertIsNone(valid_merchandise.language_id)

    def test_product_api_and_catalog_use_current_display_labels(self):
        merchandise = Product.objects.create(
            brand=self.brand,
            type=Product.Type.MERCHANDISE,
            name="星海钥匙扣",
        )
        gift_set = Product.objects.create(
            brand=self.brand,
            type=Product.Type.GIFT_SET,
            name="星海预组",
            language=ProductLanguageOption.objects.get_or_create(name="日文")[0],
        )
        slim_sku = SKU.objects.create(
            product=self.product,
            code="SEA0002",
            unit="盒",
            pack_type=SKU.PackType.SLIM,
        )
        fat_sku = SKU.objects.create(
            product=self.product,
            code="SEA0003",
            unit="盒",
            pack_type=SKU.PackType.FAT,
        )
        empty_sku = SKU.objects.create(
            product=gift_set,
            code="SEA0004",
            unit="盒",
            pack_type=SKU.PackType.EMPTY,
        )
        blank_sku = SKU.objects.create(product=merchandise, code="SEA0005", unit="个")

        self.client.force_login(self.make_user(ROLE_SALES))
        products_response = self.client.get("/api/v1/products")
        self.assertEqual(products_response.status_code, 200)
        products = {item["name"]: item for item in products_response.json()["items"]}
        expected = {
            self.product.name: (Product.Type.BOOSTER, "补充包"),
            merchandise.name: (Product.Type.MERCHANDISE, "周边"),
            gift_set.name: (Product.Type.GIFT_SET, "礼盒·预组"),
        }
        for name, (product_type, label) in expected.items():
            with self.subTest(api_product=name):
                item = products[name]
                self.assertEqual((item["type"], item["type_label"]), (product_type, label))
                self.assertNotIn("contains_cards", item)
                for sku in item["skus"]:
                    self.assertNotIn("contains_cards", sku)

        skus_response = self.client.get("/api/v1/skus")
        self.assertEqual(skus_response.status_code, 200)
        skus = {item["code"]: item for item in skus_response.json()["items"]}
        self.assertEqual(skus[slim_sku.code]["pack_type"], SKU.PackType.SLIM)
        self.assertEqual(skus[slim_sku.code]["pack_type_label"], "瘦包")
        self.assertEqual(skus[fat_sku.code]["pack_type_label"], "肥包")
        self.assertEqual(skus[empty_sku.code]["pack_type_label"], "空")
        self.assertEqual(skus[blank_sku.code]["pack_type"], "")
        self.assertEqual(skus[blank_sku.code]["pack_type_label"], "")
        for sku in skus.values():
            self.assertNotIn("contains_cards", sku)
            self.assertNotIn("contains_cards", sku["product"])

        catalog = self.client.get("/catalog/")
        self.assertEqual(catalog.status_code, 200)
        self.assertContains(catalog, "礼盒·预组")
        self.assertContains(catalog, "sku.pack_type_label")
        self.assertContains(catalog, "未设置")
        self.assertNotContains(catalog, ">slim<")
        self.assertNotContains(catalog, ">fat<")

    def test_catalog_create_retries_duplicate_barcode_confirmation_and_replays(self):
        sales_client = Client()
        sales_client.force_login(self.make_user(ROLE_SALES))
        payload = {
            "brand_id": self.brand.pk,
            "type": Product.Type.BOOSTER,
            "series": "第二弹",
            "name": "API新产品",
            "language_id": self.product.language_id,
            "official_code": "API01",
            "aliases": ["API别名"],
            "skus": [{
                "code": "SEA1001",
                "unit": "盒",
                "barcode": self.sku.barcode,
                "confirm_duplicate_barcode": False,
            }],
        }
        key = str(uuid4())
        denied = sales_client.post(
            "/api/v1/products",
            data=json.dumps({**payload, "operation_key": key}),
            content_type="application/json",
        )
        self.assertEqual(denied.status_code, 403)

        admin = User.objects.create_superuser(
            username="catalog-write-admin", email="catalog-write-admin@example.test", password="admin password"
        )
        client = Client()
        client.force_login(admin)
        languages = client.get("/api/v1/product-languages")
        self.assertEqual(languages.status_code, 200)
        self.assertIn("简中", [row["name"] for row in languages.json()["items"]])
        suggestion = client.get("/api/v1/skus/number-suggestion", {"brand_id": self.brand.pk})
        self.assertEqual(suggestion.json()["suggested_code"], "SEA0002")

        before = (Product.objects.count(), SKU.objects.count(), BusinessOperation.objects.count())
        duplicate = client.post(
            "/api/v1/products",
            data=json.dumps({**payload, "operation_key": key}),
            content_type="application/json",
        )
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.json()["error"]["code"], "duplicate_barcode_confirmation_required")
        self.assertEqual(duplicate.json()["error"]["details"]["candidates"][0]["code"], self.sku.code)
        self.assertEqual((Product.objects.count(), SKU.objects.count(), BusinessOperation.objects.count()), before)

        payload["skus"][0]["confirm_duplicate_barcode"] = True
        confirmed = client.post(
            "/api/v1/products",
            data=json.dumps({**payload, "operation_key": key}),
            content_type="application/json",
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        replay = client.post(
            "/api/v1/products",
            data=json.dumps({**payload, "operation_key": key}),
            content_type="application/json",
        )
        self.assertEqual(replay.json(), confirmed.json())
        self.assertEqual(Product.objects.count(), before[0] + 1)
        self.assertEqual(SKU.objects.count(), before[1] + 1)

        payload["name"] = "同键不同输入"
        conflict = client.post(
            "/api/v1/products",
            data=json.dumps({**payload, "operation_key": key}),
            content_type="application/json",
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"]["code"], "idempotency_conflict")

        sku_payload = {
            "operation_key": str(uuid4()),
            "product_id": confirmed.json()["product_id"],
            "code": "SEA1002",
            "unit": "盒",
        }
        new_sku = client.post(
            "/api/v1/skus",
            data=json.dumps(sku_payload),
            content_type="application/json",
        )
        self.assertEqual(new_sku.status_code, 200, new_sku.content)
        self.assertEqual(client.post(
            "/api/v1/skus",
            data=json.dumps(sku_payload),
            content_type="application/json",
        ).json(), new_sku.json())

    def test_product_inventory_is_opt_in_and_requires_inventory_permission(self):
        warehouse = Warehouse.objects.create(name="测试库存仓", address="合成地址")
        InventoryBalance.objects.create(
            sku=self.sku,
            warehouse=warehouse,
            on_hand=5,
            occupied=1,
            reserved=1,
            cost_amount="10.000",
        )
        admin = User.objects.create_superuser(
            username="catalog-inventory-admin", email="catalog-inventory-admin@example.test", password="admin password"
        )
        client = Client()
        client.force_login(admin)
        default = client.get("/api/v1/products", {"page_size": 1})
        self.assertNotIn("inventory", default.json()["items"][0]["skus"][0])
        included = client.get("/api/v1/products", {"page_size": 1, "include_inventory": "true"})
        row = included.json()["items"][0]["skus"][0]["inventory"][0]
        self.assertEqual((row["warehouse"], row["available"], row["unit"]), (warehouse.name, 3, self.sku.unit))

        catalog_only = self.make_user()
        catalog_only.user_permissions.set(Permission.objects.filter(
            content_type__app_label="crm",
            codename__in=("view_brand", "view_product", "view_sku"),
        ))
        client.force_login(catalog_only)
        denied = client.get("/api/v1/products", {"include_inventory": "true"})
        self.assertEqual(denied.status_code, 403)

    def test_product_api_filters_matching_sku_status_without_changing_default_or_order_skus(self):
        inactive_on_mixed = SKU.objects.create(
            product=self.product, code="SEA0008", unit="盒", is_active=False,
        )
        inactive_product = Product.objects.create(
            brand=self.brand, type=Product.Type.MERCHANDISE, name="仅停用SKU",
        )
        inactive_only = SKU.objects.create(
            product=inactive_product, code="SEA0009", unit="个", is_active=False,
        )
        no_sku_product = Product.objects.create(
            brand=self.brand, type=Product.Type.MERCHANDISE, name="暂无SKU",
        )
        self.client.force_login(self.make_user(ROLE_SALES))

        legacy = self.client.get("/api/v1/products").json()["items"]
        legacy_by_id = {item["id"]: item for item in legacy}
        self.assertEqual([sku["id"] for sku in legacy_by_id[self.product.pk]["skus"]], [self.sku.pk])
        self.assertEqual(legacy_by_id[inactive_product.pk]["skus"], [])
        self.assertIn(no_sku_product.pk, legacy_by_id)

        active = self.client.get("/api/v1/products?sku_status=active").json()["items"]
        active_by_id = {item["id"]: item for item in active}
        self.assertEqual(set(active_by_id), {self.product.pk})
        self.assertEqual([sku["id"] for sku in active_by_id[self.product.pk]["skus"]], [self.sku.pk])

        inactive = self.client.get("/api/v1/products?sku_status=inactive").json()["items"]
        inactive_by_id = {item["id"]: item for item in inactive}
        self.assertEqual(set(inactive_by_id), {self.product.pk, inactive_product.pk})
        self.assertEqual([sku["id"] for sku in inactive_by_id[self.product.pk]["skus"]], [inactive_on_mixed.pk])
        self.assertEqual([sku["id"] for sku in inactive_by_id[inactive_product.pk]["skus"]], [inactive_only.pk])

        all_statuses = self.client.get("/api/v1/products?sku_status=all").json()["items"]
        all_by_id = {item["id"]: item for item in all_statuses}
        self.assertEqual(set(all_by_id), {self.product.pk, inactive_product.pk})
        self.assertEqual({sku["is_active"] for sku in all_by_id[self.product.pk]["skus"]}, {True, False})

        invalid = self.client.get("/api/v1/products?sku_status=disabled")
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["error"]["code"], "invalid_filter")
        order_skus = self.client.get("/api/v1/skus").json()["items"]
        self.assertNotIn(inactive_on_mixed.pk, {sku["id"] for sku in order_skus})
        self.assertNotIn(inactive_only.pk, {sku["id"] for sku in order_skus})

    def test_sku_inner_box_spec_stays_separate_in_search_catalog_and_api(self):
        twenty_boxes = SKU.objects.create(
            product=self.product,
            code="SEA0006",
            unit="盒",
            box_spec="20中盒/箱",
            inner_box_spec="18包/中盒",
            pack_spec="4张/包",
        )
        forty_boxes = SKU.objects.create(
            product=self.product,
            code="SEA0007",
            unit="盒",
            box_spec="40中盒/箱",
            inner_box_spec="18包/中盒",
            pack_spec="4张/包",
        )

        self.assertEqual(
            {item["code"] for item in search_skus({"q": "18包/中盒"})},
            {twenty_boxes.code, forty_boxes.code},
        )

        self.client.force_login(self.make_user(ROLE_SALES))
        products = self.client.get("/api/v1/products").json()["items"]
        product = next(item for item in products if item["id"] == self.product.pk)
        embedded_skus = {item["code"]: item for item in product["skus"]}
        skus = self.client.get("/api/v1/skus").json()["items"]
        api_skus = {item["code"]: item for item in skus}

        for sku, box_spec in ((twenty_boxes, "20中盒/箱"), (forty_boxes, "40中盒/箱")):
            with self.subTest(code=sku.code):
                for item in (embedded_skus[sku.code], api_skus[sku.code]):
                    self.assertEqual(item["box_spec"], box_spec)
                    self.assertEqual(item["inner_box_spec"], "18包/中盒")
                    self.assertEqual(item["pack_spec"], "4张/包")

        catalog = self.client.get("/catalog/")
        self.assertEqual(catalog.status_code, 200)
        catalog_items = {item["code"]: item for item in catalog.context["items"]}
        self.assertEqual(catalog_items[twenty_boxes.code]["box_spec"], "20中盒/箱")
        self.assertEqual(catalog_items[forty_boxes.code]["box_spec"], "40中盒/箱")
        self.assertContains(catalog, "sku.box_spec")
        self.assertContains(catalog, "sku.inner_box_spec")
        self.assertContains(catalog, "sku.pack_spec")

    def test_product_admin_add_edit_series_choice_and_alias_search(self):
        admin_user = User.objects.create_superuser(
            username="catalog-admin", email="catalog-admin@example.test", password="admin password"
        )
        self.client.force_login(admin_user)
        add_url = "/admin/crm/product/add/"
        page = self.client.get(add_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '<datalist id="id_product_series_options">', html=False)
        self.assertContains(page, '<option value="第一弹"></option>', html=False)
        self.assertContains(page, 'name="aliases"')
        self.assertNotContains(page, 'name="contains_cards"')

        create_data = {
            "brand": self.brand.pk,
            "type": Product.Type.BOOSTER,
            "series": "第一弹",
            "name": "网页补充包",
            "language": ProductLanguageOption.objects.get_or_create(name="简中")[0].pk,
            "official_code": "WEB01",
            "aliases": "17.5\n太晶",
            "is_active": "on",
            "_save": "保存",
        }
        response = self.client.post(add_url, create_data)
        self.assertEqual(response.status_code, 302)
        product = Product.objects.get(name="网页补充包")
        self.assertEqual(product.aliases, ["17.5", "太晶"])

        change_url = f"/admin/crm/product/{product.pk}/change/"
        page = self.client.get(change_url)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["adminform"].form["aliases"].value(), "17.5\n太晶")
        self.assertNotIn("contains_cards", page.context["adminform"].form.fields)
        self.assertNotContains(page, 'name="contains_cards"')
        create_data["aliases"] = "18.0\n勇者"
        response = self.client.post(change_url, create_data)
        self.assertEqual(response.status_code, 302)
        product.refresh_from_db()
        self.assertEqual(product.aliases, ["18.0", "勇者"])
        self.assertEqual(search_products({"q": "勇者"})[0]["id"], product.pk)

    def test_sku_admin_add_edit_aliases_search_and_pack_type_labels(self):
        admin_user = User.objects.create_superuser(
            username="sku-admin", email="sku-admin@example.test", password="admin password"
        )
        self.client.force_login(admin_user)
        add_url = "/admin/crm/sku/add/"
        page = self.client.get(add_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'name="aliases"')
        self.assertContains(page, 'name="inner_box_spec"')
        self.assertContains(page, "瘦包")
        self.assertNotContains(page, ">slim<")

        create_data = {
            "product": self.product.pk,
            "code": "SEA0002",
            "box_spec": "20中盒/箱",
            "inner_box_spec": "18包/中盒",
            "pack_spec": "4张/包",
            "pack_type": SKU.PackType.SLIM,
            "unit": "盒",
            "barcode": "0099887766",
            "aliases": "整盒\n特别版",
            "is_active": "on",
            "_save": "保存",
        }
        response = self.client.post(add_url, create_data)
        self.assertEqual(response.status_code, 302)
        sku = SKU.objects.get(code="SEA0002")
        self.assertEqual(sku.aliases, ["整盒", "特别版"])
        self.assertEqual(sku.inner_box_spec, "18包/中盒")
        SKU.objects.create(
            product=self.product,
            code="SEA0003",
            unit="包",
            pack_type=SKU.PackType.FAT,
        )
        listing = self.client.get("/admin/crm/sku/")
        self.assertContains(listing, "瘦包")
        self.assertContains(listing, "肥包")
        self.assertNotContains(listing, ">slim<")
        self.assertNotContains(listing, ">fat<")

        change_url = f"/admin/crm/sku/{sku.pk}/change/"
        page = self.client.get(change_url)
        self.assertEqual(page.status_code, 200)
        form = page.context["adminform"].form
        self.assertEqual(form["aliases"].value(), "整盒\n特别版")
        self.assertEqual(form["inner_box_spec"].value(), "18包/中盒")
        self.assertIn("瘦包", form["pack_type"].as_widget())
        self.assertNotIn(">slim<", form["pack_type"].as_widget())
        create_data["aliases"] = "整盒\n限定"
        create_data["inner_box_spec"] = "20包/中盒"
        response = self.client.post(change_url, create_data)
        self.assertEqual(response.status_code, 302)
        sku.refresh_from_db()
        self.assertEqual(sku.aliases, ["整盒", "限定"])
        self.assertEqual(sku.inner_box_spec, "20包/中盒")
        self.assertEqual(search_skus({"q": "限定"})[0]["id"], sku.pk)

        blank_data = {
            **create_data,
            "code": "SEA0008",
            "box_spec": "",
            "inner_box_spec": "",
            "pack_spec": "",
            "pack_type": "",
            "unit": "盒",
            "barcode": "",
            "aliases": "",
        }
        response = self.client.post(add_url, blank_data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SKU.objects.get(code="SEA0008").inner_box_spec, "")

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
