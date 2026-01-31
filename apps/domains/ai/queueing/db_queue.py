# apps/domains/ai/queueing/db_queue.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.domains.ai.models import AIJobModel
from apps.domains.ai.queueing.interfaces import JobQueue, ClaimedJob


@dataclass(frozen=True)
class DBQueueConfig:
    visibility_timeout_sec: int = 120  # lease duration
    heartbeat_grace_sec: int = 0       # reserved for future
    default_max_attempts: int = 5
    base_backoff_sec: int = 2
    max_backoff_sec: int = 120


class DBJobQueue(JobQueue):
    """
    SQS 스타일 lease/visibility timeout을 DB로 구현.
    - DB가 SSOT
    - Worker stateless
    - crash 시 lease 만료로 자동 재처리
    """

    def __init__(self, cfg: Optional[DBQueueConfig] = None):
        self.cfg = cfg or DBQueueConfig()

    def publish(self, *, job_id: str) -> None:
        """
        DBQueue에서 publish는 '처리 가능 상태로 만드는 것' 정도만 수행.
        job row 자체는 gateway에서 이미 생성됨.
        """
        now = timezone.now()
        AIJobModel.objects.filter(job_id=job_id).update(
            status="PENDING",
            next_run_at=now,
        )

    @transaction.atomic
    def claim(self, *, worker_id: str) -> Optional[ClaimedJob]:
        now = timezone.now()

        # 0) stale RUNNING 회수(lease 만료된 작업을 PENDING으로 되돌림)
        AIJobModel.objects.select_for_update().filter(
            status="RUNNING",
            lease_expires_at__isnull=False,
            lease_expires_at__lt=now,
        ).update(
            status="PENDING",
            locked_by=None,
            locked_at=None,
            lease_expires_at=None,
        )

        # 1) claim 후보: PENDING + next_run_at <= now
        qs = (
            AIJobModel.objects.select_for_update(skip_locked=True)
            .filter(
                status="PENDING",
                next_run_at__lte=now,
            )
            .order_by("next_run_at", "created_at")
        )

        job = qs.first()
        if not job:
            return None

        # 2) attempt 증가 + lease 발급
        attempt = int(job.attempt_count or 0) + 1
        max_attempts = int(job.max_attempts or self.cfg.default_max_attempts)
        if attempt > max_attempts:
            job.status = "FAILED"
            job.error_message = job.error_message or "max_attempts_exceeded"
            job.last_error = job.last_error or "max_attempts_exceeded"
            job.save(update_fields=["status", "error_message", "last_error", "updated_at"])
            return None

        lease_expires = now + timedelta(seconds=self.cfg.visibility_timeout_sec)
        job.status = "RUNNING"
        job.attempt_count = attempt
        job.max_attempts = max_attempts
        job.locked_by = str(worker_id)
        job.locked_at = now
        job.lease_expires_at = lease_expires
        job.last_heartbeat_at = now
        job.save(
            update_fields=[
                "status",
                "attempt_count",
                "max_attempts",
                "locked_by",
                "locked_at",
                "lease_expires_at",
                "last_heartbeat_at",
                "updated_at",
            ]
        )

        return ClaimedJob(
            job_id=job.job_id,
            job_type=job.job_type,
            payload=job.payload,
            tenant_id=job.tenant_id,
            source_domain=job.source_domain,
            source_id=job.source_id,
            locked_by=job.locked_by,
        )

    def heartbeat(self, *, job_id: str, worker_id: str) -> None:
        now = timezone.now()
        # heartbeat는 운영 확장용(현재 worker가 별도 heartbeat를 치지 않아도 lease만료로 복구됨)
        AIJobModel.objects.filter(job_id=job_id, locked_by=str(worker_id), status="RUNNING").update(
            last_heartbeat_at=now,
            updated_at=now,
        )

    def mark_done(self, *, job_id: str) -> None:
        AIJobModel.objects.filter(job_id=job_id).update(
            status="DONE",
            error_message="",
            updated_at=timezone.now(),
        )

    def mark_failed(self, *, job_id: str, error: str, retryable: bool = True) -> None:
        """
        실패 처리 + retry/backoff 스케줄링
        - retryable=True & attempt_count < max_attempts 면 PENDING으로 되돌리고 next_run_at을 backoff로 설정
        - 아니면 FAILED 확정
        """
        now = timezone.now()
        job = AIJobModel.objects.filter(job_id=job_id).first()
        if not job:
            return

        attempt = int(job.attempt_count or 0)
        max_attempts = int(job.max_attempts or self.cfg.default_max_attempts)

        # backoff 계산 (2^attempt * base, cap)
        backoff = min(self.cfg.max_backoff_sec, (2 ** max(0, attempt - 1)) * self.cfg.base_backoff_sec)
        next_run = now + timedelta(seconds=int(backoff))

        if retryable and attempt < max_attempts:
            AIJobModel.objects.filter(job_id=job_id).update(
                status="PENDING",
                last_error=str(error or "")[:5000],
                error_message=str(error or "")[:5000],
                next_run_at=next_run,
                locked_by=None,
                locked_at=None,
                lease_expires_at=None,
                updated_at=now,
            )
            return

        AIJobModel.objects.filter(job_id=job_id).update(
            status="FAILED",
            last_error=str(error or "")[:5000],
            error_message=str(error or "")[:5000],
            locked_by=None,
            locked_at=None,
            lease_expires_at=None,
            updated_at=now,
        )
