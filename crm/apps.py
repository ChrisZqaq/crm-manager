from django.apps import AppConfig
from django.db.models.signals import post_migrate


class CrmConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'crm'

    def ready(self):
        from .permissions import create_fixed_role_groups, ensure_business_control

        post_migrate.connect(
            ensure_business_control,
            sender=self,
            dispatch_uid="crm.ensure_business_control",
        )

        post_migrate.connect(
            create_fixed_role_groups,
            sender=self,
            dispatch_uid='crm.create_fixed_role_groups',
        )
