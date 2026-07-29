from __future__ import annotations

from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.models import ProductUsageDailyActor, ProductUsageEvent


def _parse_date(value: str, option: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CommandError(f"{option} must use YYYY-MM-DD") from exc


class Command(BaseCommand):
    help = "Dry-run or purge product analytics rows only."

    def add_arguments(self, parser):
        parser.add_argument("--before", required=True)
        parser.add_argument("--daily-before")
        parser.add_argument("--execute", action="store_true")

    def handle(self, *args, **options):
        raw_before = _parse_date(options["before"], "--before")
        daily_before = (
            _parse_date(options["daily_before"], "--daily-before")
            if options.get("daily_before")
            else None
        )
        raw_qs = ProductUsageEvent.objects.filter(occurred_at__date__lt=raw_before)
        raw_count = raw_qs.count()
        tenant_count = raw_qs.values("tenant_id").distinct().count()
        oldest = raw_qs.order_by("occurred_at").values_list("occurred_at", flat=True).first()
        newest = raw_qs.order_by("-occurred_at").values_list("occurred_at", flat=True).first()
        source_days = set(raw_qs.dates("occurred_at", "day"))
        rolled_days = set(
            ProductUsageDailyActor.objects.filter(day__in=source_days)
            .values_list("day", flat=True)
            .distinct()
        )
        missing_days = sorted(
            item.date() if hasattr(item, "date") else item
            for item in source_days
            if (item.date() if hasattr(item, "date") else item) not in rolled_days
        )

        daily_qs = ProductUsageDailyActor.objects.none()
        if daily_before is not None:
            daily_qs = ProductUsageDailyActor.objects.filter(day__lt=daily_before)
        daily_count = daily_qs.count()

        self.stdout.write(
            f"mode={'execute' if options['execute'] else 'dry-run'} "
            f"raw_before={raw_before} raw_events={raw_count} "
            f"tenants={tenant_count} oldest={oldest} newest={newest} "
            f"daily_before={daily_before} daily_rows={daily_count}"
        )
        if missing_days:
            raise CommandError(
                "raw purge blocked; rollup missing for: "
                + ", ".join(str(item) for item in missing_days)
            )
        if not options["execute"]:
            self.stdout.write("dry-run only; no rows deleted")
            return

        with transaction.atomic():
            deleted_raw, _ = raw_qs.delete()
            deleted_daily, _ = daily_qs.delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"deleted raw_events={deleted_raw} daily_rows={deleted_daily}"
            )
        )
