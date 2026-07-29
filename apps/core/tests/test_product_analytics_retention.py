from __future__ import annotations

from datetime import datetime, time, timedelta
from io import StringIO
from uuid import uuid4

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.core.models import (
    ProductUsageDailyActor,
    ProductUsageEvent,
    Tenant,
)


class ProductUsageRetentionTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            code="analytics-retention",
            name="Analytics Retention",
            is_active=True,
        )

    def raw_event(self, *, occurred_at):
        return ProductUsageEvent.objects.create(
            event_id=uuid4(),
            tenant=self.tenant,
            actor_hash="a" * 64,
            role="teacher",
            audience_group="teacher_staff",
            session_id=uuid4(),
            view_id=uuid4(),
            event_type="screen_view",
            feature_id="attendance.mark",
            screen_id="teacher.attendance.home",
            surface="teacher",
            route_template="/teacher/attendance",
            device_class="desktop",
            client_release="test-release",
            catalog_version="2026-07-29",
            occurred_at=occurred_at,
        )

    def test_rollup_is_idempotent(self):
        target = timezone.localdate() - timedelta(days=1)
        occurred_at = timezone.make_aware(
            datetime.combine(target, time(hour=12)),
            timezone.get_current_timezone(),
        )
        self.raw_event(occurred_at=occurred_at)
        self.raw_event(occurred_at=occurred_at + timedelta(minutes=1))

        call_command("rollup_product_usage", target_date=str(target))
        first = list(
            ProductUsageDailyActor.objects.values_list("count", "first_at", "last_at")
        )
        call_command("rollup_product_usage", target_date=str(target))
        second = list(
            ProductUsageDailyActor.objects.values_list("count", "first_at", "last_at")
        )

        self.assertEqual(first, second)
        self.assertEqual(first[0][0], 2)

    def test_purge_defaults_to_dry_run_and_requires_rollup(self):
        target = timezone.localdate() - timedelta(days=40)
        occurred_at = timezone.make_aware(
            datetime.combine(target, time(hour=12)),
            timezone.get_current_timezone(),
        )
        self.raw_event(occurred_at=occurred_at)
        before = target + timedelta(days=1)

        stdout = StringIO()
        with self.assertRaisesMessage(Exception, "rollup missing"):
            call_command(
                "purge_product_usage",
                before=str(before),
                stdout=stdout,
            )
        self.assertEqual(ProductUsageEvent.objects.count(), 1)

        call_command("rollup_product_usage", target_date=str(target))
        stdout = StringIO()
        call_command(
            "purge_product_usage",
            before=str(before),
            stdout=stdout,
        )
        self.assertIn("dry-run only", stdout.getvalue())
        self.assertEqual(ProductUsageEvent.objects.count(), 1)

        call_command(
            "purge_product_usage",
            before=str(before),
            execute=True,
        )
        self.assertEqual(ProductUsageEvent.objects.count(), 0)
        self.assertEqual(ProductUsageDailyActor.objects.count(), 1)
