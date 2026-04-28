# PATH: apps/domains/fees/management/commands/mark_overdue_invoices.py
"""
일일 연체 전환 cron.
  python manage.py mark_overdue_invoices
EventBridge 또는 systemd timer에서 매일 1회 호출.
"""
from django.core.management.base import BaseCommand

from apps.domains.fees.services import mark_overdue_invoices


class Command(BaseCommand):
    help = "납부기한 경과한 PENDING/PARTIAL 청구서를 OVERDUE로 전환합니다."

    def handle(self, *args, **options):
        updated = mark_overdue_invoices()
        self.stdout.write(self.style.SUCCESS(f"OVERDUE 전환: {updated}건"))
