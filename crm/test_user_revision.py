from datetime import date
from uuid import uuid4

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase


MIGRATE_FROM = ("crm", "0013_payment_first_evidence_registered_at")
MIGRATE_TO = ("crm", "0014_user_revision_1009")


class UserRevisionDataMigrationTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        MigrationExecutor(connection).migrate([MIGRATE_FROM])
        self.old_apps = MigrationExecutor(connection).loader.project_state([MIGRATE_FROM]).apps

    def tearDown(self):
        MigrationExecutor(connection).migrate([MIGRATE_TO])
        super().tearDown()

    def _legacy_records(self, language):
        app_label, model_name = settings.AUTH_USER_MODEL.split(".")
        User = self.old_apps.get_model(app_label, model_name)
        Brand = self.old_apps.get_model("crm", "Brand")
        Product = self.old_apps.get_model("crm", "Product")
        Customer = self.old_apps.get_model("crm", "Customer")
        AccountProfile = self.old_apps.get_model("crm", "AccountProfile")
        BusinessOperation = self.old_apps.get_model("crm", "BusinessOperation")

        user = User.objects.create(username="migration-owner", password="!")
        brand = Brand.objects.create(name="Migration IP", sku_prefix="MIG")
        product = Product.objects.create(
            brand=brand,
            type="booster",
            series="Legacy",
            name="Migration Booster",
            language=language,
        )
        customer = Customer.objects.create(
            owner=user,
            name="Legacy customer",
            contact="synthetic contact",
            birthday=date(1990, 1, 2),
            interests="legacy interests",
            hobbies="legacy hobbies",
        )
        profile = AccountProfile.objects.create(user=user, birthday_reminder_enabled=False)
        operation = BusinessOperation.objects.create(
            operation_key=uuid4(),
            sequence=11,
            actor=user,
            action="historical_profile",
            input_hash="a" * 64,
            payload={"keep": "history"},
            account_profile=profile,
        )
        return {
            "product_id": product.pk,
            "customer_id": customer.pk,
            "profile_id": profile.pk,
            "operation_id": operation.pk,
            "user_id": user.pk,
        }

    def test_migration_preserves_notes_history_and_maps_standard_language(self):
        ids = self._legacy_records("简中")
        MigrationExecutor(connection).migrate([MIGRATE_TO])
        apps = MigrationExecutor(connection).loader.project_state([MIGRATE_TO]).apps

        Product = apps.get_model("crm", "Product")
        ProductLanguageOption = apps.get_model("crm", "ProductLanguageOption")
        Customer = apps.get_model("crm", "Customer")
        AccountProfile = apps.get_model("crm", "AccountProfile")
        BusinessOperation = apps.get_model("crm", "BusinessOperation")
        product = Product.objects.get(pk=ids["product_id"])
        customer = Customer.objects.get(pk=ids["customer_id"])
        profile = AccountProfile.objects.get(pk=ids["profile_id"])
        operation = BusinessOperation.objects.get(pk=ids["operation_id"])

        self.assertEqual(product.language.name, "简中")
        self.assertEqual(ProductLanguageOption.objects.count(), 4)
        self.assertEqual((customer.note1, customer.note2), ("legacy interests", "legacy hobbies"))
        self.assertEqual((customer.invoice_title, customer.invoice_credit_code, customer.invoice_email), ("", "", ""))
        self.assertNotIn("birthday", {field.name for field in Customer._meta.get_fields()})
        self.assertNotIn("birthday_reminder_enabled", {field.name for field in AccountProfile._meta.get_fields()})
        self.assertEqual(profile.user_id, ids["user_id"])
        self.assertEqual((operation.account_profile_id, operation.sequence, operation.payload), (
            profile.pk, 11, {"keep": "history"},
        ))

    def test_unknown_legacy_language_aborts_without_dropping_legacy_rows(self):
        ids = self._legacy_records("简体中文")

        with self.assertRaisesRegex(RuntimeError, "简体中文"):
            MigrationExecutor(connection).migrate([MIGRATE_TO])

        self.assertNotIn(MIGRATE_TO, MigrationRecorder(connection).applied_migrations())
        Product = self.old_apps.get_model("crm", "Product")
        Customer = self.old_apps.get_model("crm", "Customer")
        product = Product.objects.get(pk=ids["product_id"])
        customer = Customer.objects.get(pk=ids["customer_id"])
        self.assertEqual(product.language, "简体中文")
        self.assertEqual((customer.interests, customer.hobbies, customer.birthday), (
            "legacy interests", "legacy hobbies", date(1990, 1, 2),
        ))
        product.delete()
