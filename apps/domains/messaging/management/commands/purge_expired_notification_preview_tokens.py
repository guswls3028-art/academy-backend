"""Delete expired manual-notification preview payloads."""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.domains.messaging.models import NotificationPreviewToken
from apps.domains.messaging.retention import purge_expired_preview_tokens


class Command(BaseCommand):
    help = "Delete NotificationPreviewToken rows after their five-minute TTL."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--batch-size", type=int, default=500)

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        if batch_size < 1 or batch_size > 5000:
            raise CommandError("--batch-size must be between 1 and 5000")

        expired = NotificationPreviewToken.objects.filter(
            expires_at__lte=timezone.now()
        )
        total = expired.count()
        if options["dry_run"]:
            self.stdout.write(f"expired_preview_tokens={total} dry_run=true")
            return

        deleted = purge_expired_preview_tokens(batch_size=batch_size)
        self.stdout.write(
            self.style.SUCCESS(f"expired_preview_tokens_deleted={deleted}")
        )
