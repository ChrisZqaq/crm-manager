from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from crm.models import ReceiptImage


class Command(BaseCommand):
    help = "Delete unlinked receipt images older than 24 hours."

    def handle(self, *args, **options):
        expired = ReceiptImage.objects.filter(receipt__isnull=True, created_at__lt=timezone.now() - timedelta(hours=24)).order_by("id")
        removed = 0
        for image in expired.iterator():
            image.file.storage.delete(image.file.name)
            image.delete()
            removed += 1
        self.stdout.write(f"Removed {removed} expired orphan receipt image(s).")
