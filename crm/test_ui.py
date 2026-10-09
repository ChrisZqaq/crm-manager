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

        def grant(user, *permissions):
            user.user_permissions.add(*[
                Permission.objects.get(content_type__app_label="crm", codename=name)
                for name in permissions
            ])

        grant(cls.inventory, "view_inventorybalance")
        grant(cls.orders, "view_salesorder", "view_customer")
        grant(cls.verifier, "view_salesorder", "verify_payment")

    def test_workbench_is_available_without_data_permissions_and_gates_requests(self):
        self.client.force_login(self.basic)
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "/api/v1/reports/sales")
        self.assertNotContains(response, "/api/v1/reports/inventory")

        self.client.force_login(self.inventory)
        response = self.client.get("/")
        self.assertContains(response, "/api/v1/reports/inventory?low_stock=true")
        self.assertNotContains(response, "/api/v1/reports/sales")

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
