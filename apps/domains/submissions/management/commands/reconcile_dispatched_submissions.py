# apps/domains/submissions/management/commands/reconcile_dispatched_submissions.py
"""
DISPATCHED 상태에서 정체된 Submission을 복구한다.

AI Job이 이미 완료(DONE/FAILED)되었지만 callback이 누락되어
Submission이 DISPATCHED에 남아있는 경우를 감지하고 재처리한다.

사용:
  python manage.py reconcile_dispatched_submissions --dry-run
  python manage.py reconcile_dispatched_submissions --threshold-minutes 30
  python manage.py reconcile_dispatched_submissions --detect-only
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "DISPATCHED 상태에서 정체된 Submission을 감지하고 AI 결과를 재적용한다."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="감지만 하고 실제 복구하지 않음",
        )
        parser.add_argument(
            "--detect-only",
            action="store_true",
            help="정체 건만 출력하고 종료 (복구 시도 안 함)",
        )
        parser.add_argument(
            "--threshold-minutes",
            type=int,
            default=30,
            help="DISPATCHED 상태로 유지된 최소 시간 (기본 30분)",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=50,
            help="최대 처리 건수 (기본 50)",
        )

    def handle(self, **options):
        dry_run = options["dry_run"]
        detect_only = options["detect_only"]
        threshold_minutes = options["threshold_minutes"]
        limit = options["limit"]

        from apps.domains.submissions.models import Submission
        from apps.domains.ai.models import AIJobModel

        cutoff = timezone.now() - timedelta(minutes=threshold_minutes)

        stuck = list(
            Submission.objects
            .filter(status=Submission.Status.DISPATCHED, updated_at__lt=cutoff)
            .order_by("updated_at")[:limit]
        )

        self.stdout.write(
            f"RECONCILE_SCAN | found={len(stuck)} | threshold={threshold_minutes}min | cutoff={cutoff.isoformat()}"
        )

        if not stuck:
            logger.info("RECONCILE_DISPATCHED | found=0 | threshold=%dm", threshold_minutes)
            return

        # 각 submission에 대한 AI job 상태 수집
        entries = []
        for sub in stuck:
            ai_job = (
                AIJobModel.objects
                .filter(source_domain="submissions", source_id=str(sub.id))
                .order_by("-created_at")
                .first()
            )
            entries.append({
                "submission": sub,
                "ai_job": ai_job,
                "ai_status": ai_job.status if ai_job else None,
                "recoverable": ai_job and ai_job.status in ("DONE", "FAILED", "REJECTED_BAD_INPUT"),
            })

        # 상태별 집계
        no_job = [e for e in entries if not e["ai_job"]]
        pending = [e for e in entries if e["ai_job"] and not e["recoverable"]]
        recoverable = [e for e in entries if e["recoverable"]]

        self.stdout.write(
            f"RECONCILE_CLASSIFY | total={len(entries)} | "
            f"recoverable={len(recoverable)} | "
            f"ai_pending={len(pending)} | "
            f"no_ai_job={len(no_job)}"
        )

        for e in entries:
            sub = e["submission"]
            aj = e["ai_job"]
            self.stdout.write(
                f"  sub={sub.id} | tenant={sub.tenant_id} | "
                f"age={(timezone.now() - sub.updated_at).total_seconds() / 60:.0f}min | "
                f"ai_job={aj.job_id if aj else 'NONE'} | "
                f"ai_status={e['ai_status'] or 'N/A'} | "
                f"{'RECOVERABLE' if e['recoverable'] else 'SKIP'}"
            )

        if detect_only:
            logger.info(
                "RECONCILE_DISPATCHED_DETECT | total=%d | recoverable=%d | pending=%d | no_job=%d",
                len(entries), len(recoverable), len(pending), len(no_job),
            )
            return

        # 복구 실행
        recovered = 0
        errors = 0

        for e in recoverable:
            sub = e["submission"]
            aj = e["ai_job"]

            if dry_run:
                self.stdout.write(f"  [DRY] Would recover submission {sub.id}")
                recovered += 1
                continue

            try:
                from apps.domains.ai.models import AIResultModel
                ai_result = AIResultModel.objects.filter(job=aj).first()
                result_payload = ai_result.payload if ai_result else {}

                from apps.domains.ai.callbacks import dispatch_ai_result_to_domain
                dispatch_ai_result_to_domain(
                    job_id=aj.job_id,
                    status=aj.status,
                    result_payload=result_payload if isinstance(result_payload, dict) else {},
                    error=aj.error_message or None,
                    source_domain="submissions",
                    source_id=str(sub.id),
                    tier=aj.tier or "basic",
                )
                recovered += 1
                self.stdout.write(f"  [OK] Recovered submission {sub.id}")
            except Exception as exc:
                errors += 1
                self.stdout.write(f"  [ERROR] submission {sub.id}: {exc}")
                logger.exception("RECONCILE_ERROR | submission_id=%s", sub.id)

        summary = (
            f"RECONCILE_DONE | recovered={recovered} | errors={errors} | "
            f"skipped_pending={len(pending)} | skipped_no_job={len(no_job)} | "
            f"dry_run={dry_run}"
        )
        self.stdout.write(summary)
        logger.info(summary)
