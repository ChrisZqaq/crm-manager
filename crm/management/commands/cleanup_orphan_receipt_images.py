from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from crm.models import ReceiptImage


class Command(BaseCommand):
    help = "Delete unlinked receipt and payment images older than 24 hours."

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(hours=24)
        expired_ids = list(ReceiptImage.objects.filter(
            receipt__isnull=True,
            payment__isnull=True,
            created_at__lt=cutoff,
        ).order_by("id").values_list("id", flat=True))
        removed = 0
        for image_id in expired_ids:
            with transaction.atomic():
                image = ReceiptImage.objects.select_for_update().filter(
                    pk=image_id,
                    receipt__isnull=True,
                    payment__isnull=True,
                    created_at__lt=cutoff,
                ).first()
                if image is None:
                    continue
                storage, name = image.file.storage, image.file.name
                image.delete()
                transaction.on_commit(lambda storage=storage, name=name: storage.delete(name))
                removed += 1
        self.stdout.write(f"Removed {removed} expired orphan receipt image(s).")
