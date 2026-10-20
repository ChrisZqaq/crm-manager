from django.core.validators import RegexValidator
from django.db import migrations, models
from django.db.models import Q
from django.db.models.functions import Lower


class Migration(migrations.Migration):
    dependencies = [("crm", "0016_cost_at_outflow")]

    operations = [
        migrations.AddField(
            model_name="accountprofile",
            name="wecom_userid",
            field=models.CharField(
                blank=True,
                max_length=64,
                null=True,
                validators=[RegexValidator(
                    code="invalid_wecom_userid",
                    message="企微成员ID须为1至64字节，以字母或数字开头，且只含字母、数字、下划线、减号、@或点。",
                    regex=r"\A[A-Za-z0-9][A-Za-z0-9_.@-]{0,63}\Z",
                )],
            ),
        ),
        migrations.AddConstraint(
            model_name="accountprofile",
            constraint=models.UniqueConstraint(
                Lower("wecom_userid"),
                condition=Q(wecom_userid__isnull=False),
                name="crm_profile_wecomid_ci",
            ),
        ),
    ]
