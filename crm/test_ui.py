from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase

from crm.models import PaymentRecord, SalesOrder


class ApprovedWorkbenchAndPaymentUITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.basic = user_model.objects.create_user(username="ui-basic")
        cls.inventory = user_model.objects.create_user(username="ui-inventory")
        cls.orders = user_model.objects.create_user(username="ui-orders")
        cls.verifier = user_model.objects.create_user(username="ui-verifier")
        cls.catalog = user_model.objects.create_user(username="ui-catalog")
        cls.product_writer = user_model.objects.create_user(username="ui-product-writer")
        cls.order_writer = user_model.objects.create_user(username="ui-order-writer")
        cls.order_writer_without_customers = user_model.objects.create_user(username="ui-order-writer-no-customers")
        cls.admin = user_model.objects.create_superuser(username="ui-admin", email="ui-admin@example.test", password="test-only-password")

        def grant(user, *permissions):
            user.user_permissions.add(*[
                Permission.objects.get(content_type__app_label="crm", codename=name)
                for name in permissions
            ])

        grant(cls.inventory, "view_inventorybalance")
        grant(cls.orders, "view_salesorder", "view_customer")
        grant(cls.verifier, "view_salesorder", "verify_payment")
        grant(cls.catalog, "view_brand", "view_product", "view_sku")
        grant(cls.product_writer, "add_product", "add_sku", "view_brand", "view_product", "view_sku")
        grant(cls.order_writer, "add_salesorder", "view_customer", "add_customer", "view_inventorybalance")
        grant(cls.order_writer_without_customers, "add_salesorder", "view_inventorybalance")

    def test_revision_layout_contract_is_present_without_changing_permissions(self):
        self.client.force_login(self.orders)
        customer_page = self.client.get("/customers/")
        self.assertContains(customer_page, 'class="customer-detail-dialog" id="customer-detail"')
        self.assertNotContains(customer_page, 'class="data-card detail-panel')

        self.client.force_login(self.inventory)
        workbench = self.client.get("/")
        self.assertContains(workbench, "in_stock_ip_language_combinations_preview")
        self.assertContains(workbench, "low-stock-preview")
        self.assertContains(workbench, "?low_stock=true")

        self.client.force_login(self.catalog)
        self.assertContains(self.client.get("/catalog/"), "<th>IP</th><th>语言</th>")

        self.client.force_login(self.order_writer)
        order = self.client.get("/orders/new/")
        self.assertContains(order, 'class="line-quantity"')
        self.assertContains(order, 'class="order-preview data-card card-body"')
        self.assertContains(order, "available_for_order??item.available")

        self.client.force_login(self.admin)
        admin = self.client.get("/admin/")
        self.assertContains(admin, 'id="primary-navigation"')
        self.assertNotContains(admin, 'id="header"')
        self.assertContains(admin, "阿罗拉潮玩空间 CRM")
        self.assertContains(admin, "业务资料")
        product_admin = self.client.get("/admin/crm/product/")
        self.assertContains(product_admin, "商品")
        self.assertContains(product_admin, "新增商品")
        self.assertContains(product_admin, "0 件商品")
        self.assertContains(product_admin, 'class="breadcrumbs"')
        self.assertContains(product_admin, "/admin/crm/")
        self.assertContains(product_admin, "/static/crm/admin.css")
        product_admin_add = self.client.get("/admin/crm/product/add/")
        self.assertContains(product_admin_add, "新建商品")
        self.assertContains(product_admin_add, "IP")

    def test_workbench_is_available_without_data_permissions_and_gates_requests(self):
        self.client.force_login(self.basic)
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "/api/v1/reports/sales")
        self.assertNotContains(response, "/api/v1/reports/inventory")
        self.assertNotContains(response, "workbench=customer_ranking")
        self.assertNotContains(response, "workbench=day_over_day")
        self.assertNotContains(response, "workbench=ip_language_stock_count")
        self.assertNotContains(response, "include_created_today=true")
        self.assertNotContains(response, 'id="customer-created-today"')
        self.assertContains(response, "当前账号无权查看此数据")

        self.client.force_login(self.orders)
        response = self.client.get("/")
        self.assertContains(response, "workbench=customer_ranking")
        self.assertContains(response, "workbench=day_over_day")
        self.assertContains(response, "/api/v1/customers?page_size=1&include_created_today=true")
        self.assertContains(response, 'id="customer-created-today"')
        self.assertContains(response, 'id="workbench-trend-data"')
        self.assertContains(response, "new ResizeObserver(render)")
        self.assertNotContains(response, "preserveAspectRatio")
        self.assertContains(response, "网络连接失败，请稍后重试。")
        self.assertContains(response, "const fitMetric = element =>")
        self.assertContains(response, "window.addEventListener('resize',fitMetrics)")
        self.assertContains(response, "if(rate!==null) growth.setAttribute('aria-label',`增速 ${fullRate}`)")
        self.assertContains(response, "link.title=preview.label")
        self.assertContains(response, "status-preview-amount")
        self.assertContains(response, "loadOrders('awaiting_payment'")
        self.assertNotContains(response, "workbench=ip_language_stock_count")

        self.client.force_login(self.inventory)
        response = self.client.get("/")
        self.assertContains(response, "/api/v1/reports/inventory?low_stock=true")
        self.assertContains(response, "workbench=ip_language_stock_count")
        self.assertNotContains(response, "/api/v1/reports/sales")

    def test_customer_editor_and_catalog_inventory_are_permission_gated(self):
        self.client.force_login(self.orders)
        self.assertContains(self.client.get("/customers/"), 'id="customer-dialog"')

        self.client.force_login(self.catalog)
        response = self.client.get("/catalog/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "include_inventory")
        self.assertEqual(self.client.get("/catalog/new/").status_code, 403)

        self.client.force_login(self.product_writer)
        self.assertEqual(self.client.get("/catalog/new/").status_code, 200)
        self.assertContains(self.client.get("/catalog/"), 'href="/catalog/new/"')

    def test_order_editor_and_admin_management_render(self):
        self.client.force_login(self.order_writer)
        response = self.client.get("/orders/new/")
        self.assertEqual(response.status_code, 200)
        for marker in ('id="customer-mode"', 'id="requires-invoice"', 'id="more-skus"', 'data-availability'):
            self.assertContains(response, marker)

        self.client.force_login(self.admin)
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "正式期初入口")
        self.assertContains(response, "商品语言管理")

        self.client.force_login(self.order_writer_without_customers)
        response = self.client.get("/orders/new/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="search-customers"')

    def test_customer_recent_orders_are_only_requested_with_order_permission(self):
        self.client.force_login(self.basic)
        self.assertEqual(self.client.get("/customers/").status_code, 403)

        self.client.force_login(self.orders)
        response = self.client.get("/customers/")
        self.assertContains(response, "/api/v1/orders?customer_id=")

    def test_payment_verification_control_is_permission_gated(self):
        self.client.force_login(self.orders)
        response = self.client.get("/reports/payments/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "verify-payment")

        self.client.force_login(self.verifier)
        response = self.client.get("/reports/payments/")
        self.assertContains(response, "verify-payment")

    def test_status_shortcuts_keep_existing_statuses_and_selected_filters(self):
        self.client.force_login(self.orders)

        response = self.client.get("/orders/", {"status": "cancelled", "q": "sample", "verification_status": "passed"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="order-status" type="hidden" name="status" value="cancelled"')
        for value, label in SalesOrder.Status.choices:
            self.assertContains(response, f'data-order-status="{value}"')
            self.assertContains(response, label)
        self.assertContains(response, 'aria-pressed="true" class="is-active">已取消</button>')
        self.assertContains(response, 'name="q" type="search" value="sample"')
        self.assertContains(response, 'name="verification_status"')

        response = self.client.get("/reports/payments/", {"status": "problem", "q": "sample", "paused": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="payment-status" type="hidden" name="status" value="problem"')
        self.assertContains(response, 'data-payment-status="problem" aria-pressed="true" class="is-active">待处理</button>')
        for value, label in PaymentRecord.Status.choices:
            self.assertContains(response, f'data-payment-status="{value}"')
            self.assertContains(response, label)
        self.assertEqual(response.content.count(b'name="status"'), 1)
        self.assertContains(response, 'name="q" value="sample"')
        self.assertContains(response, 'name="paused"')
