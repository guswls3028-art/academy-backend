"""
fix_progress_policy_defaults
기존 ProgressPolicy의 exam_start_session_order, homework_start_session_order를
2 → 1로 업데이트. 1차시의 시험/과제도 progress 계산에 포함되도록.
"""
from django.core.management.base import BaseCommand
from apps.domains.progress.models import ProgressPolicy


class Command(BaseCommand):
    help = "Update ProgressPolicy exam/homework start order from 2 to 1"

    def handle(self, *args, **options):
        updated = ProgressPolicy.objects.filter(
            exam_start_session_order=2
        ).update(exam_start_session_order=1)
        self.stdout.write(f"exam_start_session_order: {updated} records updated")

        updated2 = ProgressPolicy.objects.filter(
            homework_start_session_order=2
        ).update(homework_start_session_order=1)
        self.stdout.write(f"homework_start_session_order: {updated2} records updated")

        self.stdout.write(self.style.SUCCESS("Done"))
