from __future__ import annotations

import logging

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Send clinic reminders whose start-minus-minutes_before time has arrived."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant-id",
            type=int,
            default=None,
            help="Only process one tenant.",
        )
        parser.add_argument(
            "--window-minutes",
            type=int,
            default=5,
            help="Grace window after the configured due time. Default: 5.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show due counts without enqueueing messages.",
        )

    def handle(self, *args, **options):
        from apps.domains.messaging.services.notification_service import send_due_clinic_reminders

        stats = send_due_clinic_reminders(
            tenant_id=options.get("tenant_id"),
            window_minutes=options["window_minutes"],
            dry_run=options["dry_run"],
        )
        line = (
            f"configs={stats['configs']} sessions_checked={stats['sessions_checked']} "
            f"sessions_due={stats['sessions_due']} attempted={stats['attempted']} "
            f"sent={stats['sent']} skipped={stats['skipped']} dry_run={stats['dry_run']}"
        )
        if stats["sessions_due"]:
            self.stdout.write(self.style.SUCCESS(line))
        else:
            self.stdout.write(line)
