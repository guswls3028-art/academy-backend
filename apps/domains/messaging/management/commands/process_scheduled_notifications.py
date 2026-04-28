# apps/support/messaging/management/commands/process_scheduled_notifications.py
"""
예약/지연 발송 처리 — 주기적 실행 (EventBridge 1분 주기).

send_at이 도래한 ScheduledNotification을 SQS로 전달.
"""

import logging
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "예약 발송 대기열에서 send_at이 도래한 알림을 처리합니다."

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=100,
            help="한 번에 처리할 최대 건수 (기본: 100)",
        )

    def handle(self, *args, **options):
        from apps.domains.messaging.scheduled import process_due_notifications

        batch_size = options["batch_size"]
        stats = process_due_notifications(batch_size=batch_size)

        if stats["processed"]:
            self.stdout.write(
                f"Processed {stats['processed']}: sent={stats['sent']}, failed={stats['failed']}"
            )
        else:
            self.stdout.write("No pending scheduled notifications.")
