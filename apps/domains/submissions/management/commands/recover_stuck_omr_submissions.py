"""
OMR pipeline state recovery management command.

매분 cron / EventBridge 에서 호출. 30 분 이상 SUBMITTED / DISPATCHED /
EXTRACTING / GRADING 에 박혀있는 OMR submission 을 FAILED 로 자동 전환하고
운영 알람용 audit 로깅을 남긴다.

사용:
    python manage.py recover_stuck_omr_submissions          # 실행
    python manage.py recover_stuck_omr_submissions --dry-run  # 점검
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.domains.submissions.omr_pipeline.services.state_recovery import (
    recover_stuck_submissions,
)


class Command(BaseCommand):
    help = "Auto-fail OMR submissions stuck in pending states beyond timeout."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only report stuck submissions, do not change status.",
        )
        parser.add_argument(
            "--actor",
            default="cron.recover_stuck_omr",
            help="Audit actor tag written to meta.state_recovery.actor.",
        )

    def handle(self, *args, **options):
        report = recover_stuck_submissions(
            actor=options["actor"],
            dry_run=options["dry_run"],
        )
        self.stdout.write(
            f"detected={len(report.detected)} "
            f"recovered={len(report.recovered)} "
            f"skipped={len(report.skipped)} "
            f"failed_transitions={len(report.failed_transitions)}"
        )
        for alert in report.detected:
            self.stdout.write(
                f"  sub={alert.submission_id} status={alert.status} "
                f"age_min={alert.age_min} tenant={alert.tenant_id}"
            )
        for sub_id, err in report.failed_transitions[:20]:
            self.stdout.write(
                self.style.WARNING(f"  transition failed sub={sub_id} reason={err}")
            )
