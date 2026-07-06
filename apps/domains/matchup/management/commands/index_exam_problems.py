# PATH: apps/domains/matchup/management/commands/index_exam_problems.py
"""
기존 시험 문제를 매치업 DB에 일괄 인덱싱.

Usage:
  python manage.py index_exam_problems                  # 전체 테넌트
  python manage.py index_exam_problems --tenant-id 1    # 특정 테넌트
  python manage.py index_exam_problems --dry-run        # 실행 없이 확인만
"""
from django.core.management.base import BaseCommand

from apps.support.matchup.exam_problem_index_dependencies import (
    dispatch_matchup_index_exam_job,
    template_exam_problem_rows,
)


class Command(BaseCommand):
    help = "기존 시험 문제를 매치업 인덱스에 등록 (AI job 디스패치)"

    def add_arguments(self, parser):
        parser.add_argument("--tenant-id", type=int, default=None, help="특정 테넌트만 처리")
        parser.add_argument("--dry-run", action="store_true", help="실행 없이 대상만 출력")

    def handle(self, *args, **options):
        tenant_id = options["tenant_id"]
        dry_run = options["dry_run"]

        exams = template_exam_problem_rows(tenant_id=tenant_id)
        self.stdout.write(f"Found {len(exams)} template exams with questions")

        if dry_run:
            for eid, tid, title, q_count in exams:
                self.stdout.write(f"  [DRY] tenant={tid} exam={eid} title={title} questions={q_count}")
            return

        dispatched = 0
        failed = 0
        for eid, tid, title, q_count in exams:
            try:
                result = dispatch_matchup_index_exam_job(exam_id=eid, tenant_id=tid)
                ok = result.get("ok", False) if isinstance(result, dict) else True
                if ok:
                    dispatched += 1
                    self.stdout.write(f"  [OK] exam={eid} title={title} questions={q_count}")
                else:
                    failed += 1
                    err = result.get("error", "") if isinstance(result, dict) else ""
                    self.stdout.write(self.style.WARNING(f"  [SKIP] exam={eid} error={err}"))
            except Exception as e:
                failed += 1
                self.stdout.write(self.style.ERROR(f"  [FAIL] exam={eid} error={e}"))

        self.stdout.write(self.style.SUCCESS(f"\nDispatched: {dispatched}, Failed: {failed}"))
