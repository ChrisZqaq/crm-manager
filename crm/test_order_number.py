from django.conf import settings
from django.db import connection
from django.test import TransactionTestCase
from django.db.migrations.executor import MigrationExecutor


MIGRATE_FROM = ("crm", "0018_customer_contact_optional")
MIGRATE_TO = ("crm", "0019_businesscontrol_last_sales_order_daily_sequence_and_more")


class OrderNumberMigrationTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        MigrationExecutor(connection).migrate([MIGRATE_FROM])
        apps = MigrationExecutor(connection).loader.project_state([MIGRATE_FROM]).apps
        user_app, user_model = settings.AUTH_USER_MODEL.split(".")
        User = apps.get_model(user_app, user_model)
        Channel = apps.get_model("crm", "Channel")
        SalesOrder = apps.get_model("crm", "SalesOrder")
        user = User.objects.create(username="legacy-order-migration")
        channel = Channel.objects.create(name="Synthetic migration channel")
        order = SalesOrder.objects.create(
            salesperson=user,
            channel=channel,
            delivery_mode="on_site",
            last_sequence=73,
            customer_name_snapshot="Synthetic legacy customer snapshot",
        )
        self.order_id = order.pk
        self.created_at = order.created_at

    def tearDown(self):
        MigrationExecutor(connection).migrate([MIGRATE_TO])
        super().tearDown()

    def test_migration_preserves_existing_order_without_assigning_a_number(self):
        MigrationExecutor(connection).migrate([MIGRATE_TO])
        apps = MigrationExecutor(connection).loader.project_state([MIGRATE_TO]).apps
        SalesOrder = apps.get_model("crm", "SalesOrder")
        order = SalesOrder.objects.get(pk=self.order_id)
        self.assertIsNone(order.order_number)
        self.assertEqual(order.last_sequence, 73)
        self.assertEqual(order.customer_name_snapshot, "Synthetic legacy customer snapshot")
        self.assertEqual(order.created_at, self.created_at)
