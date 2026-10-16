from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("crm", "0014_user_revision_1009")]

    operations = [
        migrations.RemoveField(model_name="sku", name="box_spec"),
        migrations.RemoveField(model_name="sku", name="inner_box_spec"),
        migrations.RemoveField(model_name="sku", name="pack_spec"),
    ]
