"""
recompute_session_progress

드리프트 해소(V1.1.2) 이후 SessionProgress.exam_passed 로직 변경 소급 적용.
기존 로직: 집계(MAX/AVG/LATEST) 기반
신규 로직: 모든 개별 시험 passed AND

이미 저장된 SessionProgress row의 exam_passed / completed 값은 재계산 없이는
그대로라서 "세션 완료 배지 + 개별 시험 클리닉 대상" 드리프트가 그대로 남는다.
본 커맨드는 전체 또는 범위 지정된 SessionProgress를 재계산한다.

사용 예:
  # 전체 재계산 (dry-run)
  python manage.py recompute_session_progress --dry-run
  # 전체 재계산 (실제 적용)
  python manage.py recompute_session_progress
  # 특정 테넌트만
  python manage.py recompute_session_progress --tenant-id 1
  # 특정 enrollment만
  python manage.py recompute_session_progress --enrollment-id 123
  # 특정 lecture만
  python manage.py recompute_session_progress --lecture-id 45
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.domains.progress.models import SessionProgress
from apps.domains.progress.services.session_calculator import (
    SessionProgressCalculator,
)


class Command(BaseCommand):
    help = (
        "기존 SessionProgress를 새 드리프트 해소 로직으로 재계산. "
        "exam_passed=완료(집계) → 개별시험 AND 로 재판정."
    )

    def add_arguments(self, parser):
        parser.add_argument("--tenant-id", type=int, default=None)
        parser.add_argument("--enrollment-id", type=int, default=None)
        parser.add_argument("--lecture-id", type=int, default=None)
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="변경 대상 수만 출력, 실제 재계산 생략",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="한 번에 처리할 row 수 (default 500)",
        )

    def handle(self, *args, **options):
        qs = SessionProgress.objects.select_related(
            "enrollment", "enrollment__tenant", "session", "session__lecture",
        )

        tenant_id = options.get("tenant_id")
        enrollment_id = options.get("enrollment_id")
        lecture_id = options.get("lecture_id")

        if tenant_id is not None:
            qs = qs.filter(enrollment__tenant_id=tenant_id)
        if enrollment_id is not None:
            qs = qs.filter(enrollment_id=enrollment_id)
        if lecture_id is not None:
            qs = qs.filter(session__lecture_id=lecture_id)

        total = qs.count()
        self.stdout.write(f"대상 SessionProgress: {total}건")

        if options["dry_run"]:
            # 드리프트 후보: 현재 completed=True지만 재계산 시 False가 될 row
            # (exam_passed 기반으로 현재 값과 결과 비교는 실제 계산 필요 →
            #  여기서는 단순히 개수만 리포트. 정밀 진단은 실제 실행 후 로그)
            already_not_completed = qs.filter(completed=False).count()
            self.stdout.write(
                f"  - 현재 completed=True: {total - already_not_completed}건"
            )
            self.stdout.write(
                f"  - 현재 completed=False: {already_not_completed}건"
            )
            self.stdout.write(self.style.WARNING("dry-run: 변경 없음"))
            return

        # 실제 재계산
        batch_size = options["batch_size"]
        changed_completed = 0
        changed_exam_passed = 0
        errors = 0
        processed = 0

        # iterator로 메모리 안전하게 순회
        for sp in qs.iterator(chunk_size=batch_size):
            try:
                before_completed = sp.completed
                before_exam_passed = sp.exam_passed

                with transaction.atomic():
                    SessionProgressCalculator.calculate(
                        enrollment_id=sp.enrollment_id,
                        session=sp.session,
                        attendance_type=sp.attendance_type or "online",
                        video_progress_rate=sp.video_progress_rate or 0,
                        homework_submitted=bool(sp.homework_submitted),
                    )
                sp.refresh_from_db(fields=["completed", "exam_passed"])

                if sp.completed != before_completed:
                    changed_completed += 1
                if sp.exam_passed != before_exam_passed:
                    changed_exam_passed += 1

            except Exception as e:
                errors += 1
                self.stdout.write(
                    self.style.ERROR(
                        f"  FAIL enrollment={sp.enrollment_id} session={sp.session_id}: {e}"
                    )
                )

            processed += 1
            if processed % batch_size == 0:
                self.stdout.write(
                    f"  진행: {processed}/{total} "
                    f"(completed 변경: {changed_completed}, "
                    f"exam_passed 변경: {changed_exam_passed}, "
                    f"에러: {errors})"
                )

        self.stdout.write(self.style.SUCCESS(
            f"완료: 처리 {processed}건, "
            f"completed 변경 {changed_completed}건, "
            f"exam_passed 변경 {changed_exam_passed}건, "
            f"에러 {errors}건"
        ))
