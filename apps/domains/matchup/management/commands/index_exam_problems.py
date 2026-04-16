# PATH: apps/domains/matchup/management/commands/index_exam_problems.py
"""
기존 시험 문제를 매치업 DB에 일괄 인덱싱.

Usage:
  python manage.py index_exam_problems                  # 전체 테넌트
  python manage.py index_exam_problems --tenant-id 1    # 특정 테넌트
  python manage.py index_exam_problems --dry-run        # 실행 없이 확인만
"""
from django.core.management.base import BaseCommand

from apps.domains.exams.models import Exam


class Command(BaseCommand):
    help = "기존 시험 문제를 매치업 인덱스에 등록 (AI job 디스패치)"

    def add_arguments(self, parser):
        parser.add_argument("--tenant-id", type=int, default=None, help="특정 테넌트만 처리")
        parser.add_argument("--dry-run", action="store_true", help="실행 없이 대상만 출력")

    def handle(self, *args, **options):
        tenant_id = options["tenant_id"]
        dry_run = options["dry_run"]

        # template 시험 중 sheet와 question이 있는 것만
        qs = Exam.objects.filter(
            exam_type=Exam.ExamType.TEMPLATE,
            sheet__isnull=False,
            sheet__total_questions__gt=0,
        ).select_related("sheet")

        if tenant_id:
            qs = qs.filter(tenant_id=tenant_id)

        exams = list(qs.values_list("id", "tenant_id", "title", "sheet__total_questions"))
        self.stdout.write(f"Found {len(exams)} template exams with questions")

        if dry_run:
            for eid, tid, title, q_count in exams:
                self.stdout.write(f"  [DRY] tenant={tid} exam={eid} title={title} questions={q_count}")
            return

        from apps.domains.ai.gateway import dispatch_job

        dispatched = 0
        failed = 0
        for eid, tid, title, q_count in exams:
            try:
                result = dispatch_job(
                    job_type="matchup_index_exam",
                    payload={
                        "exam_id": str(eid),
                        "tenant_id": str(tid),
                    },
                    tenant_id=str(tid),
                    source_domain="matchup_index",
                    source_id=str(eid),
                )
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
