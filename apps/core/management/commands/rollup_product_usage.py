from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count, Max, Min
from django.utils import timezone

from apps.core.models import ProductUsageDailyActor, ProductUsageEvent


class Command(BaseCommand):
    help = "Aggregate one local calendar day of product usage events."

    def add_arguments(self, parser):
        parser.add_argument("--date", required=True, dest="target_date")

    def handle(self, *args, **options):
        try:
            target_day = date.fromisoformat(options["target_date"])
        except ValueError as exc:
            raise CommandError("--date must use YYYY-MM-DD") from exc

        tz = timezone.get_current_timezone()
        start = timezone.make_aware(datetime.combine(target_day, time.min), tz)
        end = start + timedelta(days=1)
        source = ProductUsageEvent.objects.filter(
            occurred_at__gte=start,
            occurred_at__lt=end,
        )
        source_count = source.count()

        dimensions = [
            "tenant_id",
            "actor_hash",
            "role",
            "audience_group",
            "surface",
            "feature_id",
            "screen_id",
            "event_type",
            "cta_id",
            "action_id",
            "placement_id",
            "position_index",
            "device_class",
            "client_release",
            "catalog_version",
            "synthetic",
            "is_impersonated",
        ]
        aggregates = list(
            source.values(*dimensions).annotate(
                event_count=Count("id"),
                first_event_at=Min("occurred_at"),
                last_event_at=Max("occurred_at"),
            )
        )
        rows = [
            ProductUsageDailyActor(
                day=target_day,
                tenant_id=item["tenant_id"],
                actor_hash=item["actor_hash"],
                role=item["role"],
                audience_group=item["audience_group"],
                surface=item["surface"],
                feature_id=item["feature_id"],
                screen_id=item["screen_id"],
                event_type=item["event_type"],
                cta_id=item["cta_id"],
                action_id=item["action_id"],
                placement_id=item["placement_id"],
                position_index=(
                    item["position_index"]
                    if item["position_index"] is not None
                    else -1
                ),
                device_class=item["device_class"],
                client_release=item["client_release"],
                catalog_version=item["catalog_version"],
                synthetic=item["synthetic"],
                is_impersonated=item["is_impersonated"],
                count=item["event_count"],
                first_at=item["first_event_at"],
                last_at=item["last_event_at"],
            )
            for item in aggregates
        ]

        with transaction.atomic():
            ProductUsageDailyActor.objects.filter(day=target_day).delete()
            ProductUsageDailyActor.objects.bulk_create(rows, batch_size=1000)

        ratio = round(source_count / len(rows), 2) if rows else 0
        self.stdout.write(
            self.style.SUCCESS(
                f"day={target_day} source={source_count} "
                f"daily_rows={len(rows)} compression={ratio}:1"
            )
        )
