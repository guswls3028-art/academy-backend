# PATH: apps/domains/students/management/commands/purge_deleted_students.py
"""
삭제된 학생 중 30일 경과분 영구 삭제.

- soft delete된 학생(deleted_at 설정) 중 deleted_at이 30일 초과된 경우 영구 삭제
- cron으로 매일 실행 권장

사용:
  python manage.py purge_deleted_students
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from django.contrib.auth import get_user_model

from apps.domains.students.models import Student
from apps.domains.enrollment.models import Enrollment


class Command(BaseCommand):
    help = "30일 초과된 삭제된 학생을 영구 삭제합니다."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=30,
            help="이 기간(일) 초과 시 삭제 (기본 30)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="실제 삭제 없이 대상만 출력",
        )

    def handle(self, *args, **options):
        days = options["days"]
        dry_run = options["dry_run"]
        cutoff = timezone.now() - timedelta(days=days)

        to_purge = list(
            Student.objects.filter(deleted_at__lt=cutoff).select_related("user")
        )

        if not to_purge:
            self.stdout.write(f"삭제 대상 없음 (deleted_at < {cutoff})")
            return

        self.stdout.write(f"영구 삭제 대상: {len(to_purge)}명")
        for s in to_purge[:5]:
            self.stdout.write(f"  - {s.name} (id={s.id}, deleted_at={s.deleted_at})")
        if len(to_purge) > 5:
            self.stdout.write(f"  ... 외 {len(to_purge) - 5}명")

        if dry_run:
            self.stdout.write(self.style.WARNING("--dry-run: 실제 삭제하지 않음"))
            return

        User = get_user_model()
        deleted = 0
        with transaction.atomic():
            student_ids = [s.id for s in to_purge]
            Enrollment.objects.filter(student_id__in=student_ids).delete()
            for student in to_purge:
                user = student.user
                student.delete()
                if user:
                    user.delete()
                deleted += 1

        self.stdout.write(self.style.SUCCESS(f"영구 삭제 완료: {deleted}명"))
