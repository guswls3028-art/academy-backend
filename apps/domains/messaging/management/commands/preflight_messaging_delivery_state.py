"""Read-only preflight for the messaging delivery-state migration."""

from django.core.management.base import BaseCommand, CommandError

from apps.domains.messaging.models import ScheduledNotification


class Command(BaseCommand):
    help = "Fail when ScheduledNotification has a non-object JSON payload."

    def handle(self, *args, **options):
        malformed_count = 0
        sample_ids: list[int] = []
        rows = (
            ScheduledNotification.objects.only("id", "payload")
            .order_by("id")
            .iterator(chunk_size=500)
        )
        for notification in rows:
            if isinstance(notification.payload, dict):
                continue
            malformed_count += 1
            if len(sample_ids) < 20:
                sample_ids.append(notification.id)

        if malformed_count:
            raise CommandError(
                "messaging_delivery_state_preflight_failed:"
                f"malformed_payload_count={malformed_count}:"
                f"sample_ids={sample_ids}:sample_limit=20"
            )
        self.stdout.write(
            self.style.SUCCESS("malformed_payload_count=0")
        )
