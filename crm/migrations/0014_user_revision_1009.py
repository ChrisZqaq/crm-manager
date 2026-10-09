from django.db import migrations, models
from django.db.models import Count, Q
import django.db.models.deletion


LANGUAGES = ("简中", "日文", "英文", "韩文")


def migrate_revision_data(apps, schema_editor):
    db = schema_editor.connection.alias
    Product = apps.get_model("crm", "Product")
    ProductLanguageOption = apps.get_model("crm", "ProductLanguageOption")
    BusinessControl = apps.get_model("crm", "BusinessControl")
    OpeningBatch = apps.get_model("crm", "OpeningBatch")

    options = {
        name: ProductLanguageOption.objects.using(db).get_or_create(name=name)[0].pk
        for name in LANGUAGES
    }
    products = Product.objects.using(db)
    unsupported_counts = list(
        products
        .exclude(language_legacy__isnull=True)
        .exclude(language_legacy="")
        .exclude(language_legacy__in=LANGUAGES)
        .values("language_legacy", "type")
        .annotate(count=Count("pk"))
        .order_by("language_legacy", "type")
    )
    unsupported_ids = list(
        products
        .exclude(language_legacy__isnull=True)
        .exclude(language_legacy="")
        .exclude(language_legacy__in=LANGUAGES)
        .order_by("pk")
        .values_list("language_legacy", "pk")
    )
    required_without_language = list(
        Product.objects.using(db)
        .filter(type__in=("booster", "gift_set"))
        .filter(Q(language_legacy__isnull=True) | Q(language_legacy=""))
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    if unsupported_counts or required_without_language:
        details = []
        if unsupported_counts:
            details.append(f"未知语言按类型计数：{unsupported_counts}；语言及产品ID（最多列出50项）：{unsupported_ids[:50]}")
        if required_without_language:
            details.append(f"必填语言缺失的产品ID（最多列出50项）：{required_without_language[:50]}")
        raise RuntimeError("商品语言迁移已中止，旧数据未删除；请核对并批准映射后重试。" + "；".join(details))

    for name, option_id in options.items():
        Product.objects.using(db).filter(language_legacy=name).update(language_id=option_id)

    if OpeningBatch.objects.using(db).exists():
        BusinessControl.objects.using(db).update(opening_enabled=False)
    else:
        BusinessControl.objects.using(db).filter(opening_completed=True).update(opening_enabled=False)


def restore_language_labels(apps, schema_editor):
    db = schema_editor.connection.alias
    Product = apps.get_model("crm", "Product")
    for product_id, name in Product.objects.using(db).filter(language__isnull=False).values_list("pk", "language__name"):
        Product.objects.using(db).filter(pk=product_id).update(language_legacy=name)


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0013_payment_first_evidence_registered_at"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProductLanguageOption",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=80, unique=True, verbose_name="语言版本")),
            ],
            options={
                "verbose_name": "商品语言",
                "verbose_name_plural": "商品语言",
                "ordering": ("id",),
            },
        ),
        migrations.RemoveConstraint(
            model_name="product",
            name="crm_product_language_required",
        ),
        migrations.RenameField(
            model_name="product",
            old_name="language",
            new_name="language_legacy",
        ),
        migrations.AddField(
            model_name="product",
            name="language",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="products",
                to="crm.productlanguageoption",
                verbose_name="语言版本",
            ),
        ),
        migrations.AddField(
            model_name="businesscontrol",
            name="opening_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.RenameField(model_name="customer", old_name="interests", new_name="note1"),
        migrations.AlterField(model_name="customer", name="note1", field=models.TextField(blank=True, verbose_name="备注1")),
        migrations.RenameField(model_name="customer", old_name="hobbies", new_name="note2"),
        migrations.AlterField(model_name="customer", name="note2", field=models.TextField(blank=True, verbose_name="备注2")),
        migrations.AddField(
            model_name="customer",
            name="invoice_title",
            field=models.CharField(blank=True, max_length=255, verbose_name="发票抬头"),
        ),
        migrations.AddField(
            model_name="customer",
            name="invoice_credit_code",
            field=models.CharField(blank=True, max_length=100, verbose_name="统一社会信用代码"),
        ),
        migrations.AddField(
            model_name="customer",
            name="invoice_email",
            field=models.CharField(blank=True, max_length=254, verbose_name="发票邮箱"),
        ),
        migrations.AddField(
            model_name="salesorder",
            name="requires_invoice",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="salesorder",
            name="invoice_title",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="salesorder",
            name="invoice_credit_code",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="salesorder",
            name="invoice_email",
            field=models.CharField(blank=True, max_length=254),
        ),
        migrations.RemoveField(model_name="customer", name="birthday"),
        migrations.RemoveField(model_name="accountprofile", name="birthday_reminder_enabled"),
        migrations.RunPython(migrate_revision_data, restore_language_labels),
        migrations.RemoveField(model_name="product", name="language_legacy"),
        migrations.AddConstraint(
            model_name="product",
            constraint=models.CheckConstraint(
                condition=~Q(type__in=["booster", "gift_set"]) | Q(language__isnull=False),
                name="crm_product_language_required",
            ),
        ),
        migrations.AlterModelOptions(
            name="businesscontrol",
            options={
                "permissions": (
                    ("manage_global_low_stock", "管理全局低库存提醒"),
                    ("manage_opening", "管理正式期初入口"),
                ),
            },
        ),
    ]
