# apps/domains/ai/queueing/db_queue.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from django.db import transaction
from django.utils import timezone

from apps.domains.ai.models import AIJobModel
from apps.domains.ai.queueing.interfaces import JobQueue, ClaimedJob
from apps.domains.ai.services.status_resolver import status_for_exception


@dataclass(frozen=True)
class DBQueueConfig:
    visibility_timeout_sec: int = 120  # lease duration
    default_max_attempts: int = 5
    base_backoff_sec: int = 2
    max_backoff_sec: int = 120


class DBJobQueue(JobQueue):
    """
    SQS-style lease/visibility timeout on DB.
    - DB is SSOT
    - Worker is stateless
    - Crash recovery via lease expiry
    """

    def __init__(self, cfg: Optional[DBQueueConfig] = None):
        self.cfg = cfg or DBQueueConfig()

    def publish(self, *, job_id: str) -> None:
        now = timezone.now()
        AIJobModel.objects.filter(job_id=job_id).update(
            status="PENDING",
            next_run_at=now,
        )

    @transaction.atomic
    def claim(self, *, worker_id: str) -> Optional[ClaimedJob]:
        now = timezone.now()

        # 0) reclaim stale RUNNING jobs
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

        # 1) pick next runnable
        qs = (
            AIJobModel.objects.select_for_update(skip_locked=True)
            .filter(status="PENDING", next_run_at__lte=now)
            .order_by("next_run_at", "created_at")
        )

        job = qs.first()
        if not job:
            return None

        # 2) attempts + lease
        attempt = int(job.attempt_count or 0) + 1
        max_attempts = int(job.max_attempts or self.cfg.default_max_attempts)
        if attempt > max_attempts:
            final_status, _ = status_for_exception(job.tier or "basic")
            job.status = final_status
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
            payload=job.payload or {},
            tenant_id=job.tenant_id,
            source_domain=job.source_domain,
            source_id=job.source_id,
            locked_by=job.locked_by,
        )

    def heartbeat(self, *, job_id: str, worker_id: str) -> None:
        now = timezone.now()
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
        실패 처리 + retry/backoff
        - retryable=True & attempt_count < max_attempts => PENDING with next_run_at(backoff)
        - else => FAILED
        """
        now = timezone.now()
        job = AIJobModel.objects.filter(job_id=job_id).first()
        if not job:
            return

        attempt = int(job.attempt_count or 0)
        max_attempts = int(job.max_attempts or self.cfg.default_max_attempts)

        backoff = min(self.cfg.max_backoff_sec, (2 ** max(0, attempt - 1)) * self.cfg.base_backoff_sec)
        next_run = now + timedelta(seconds=int(backoff))

        err = str(error or "")[:5000]

        if retryable and attempt < max_attempts:
            AIJobModel.objects.filter(job_id=job_id).update(
                status="PENDING",
                last_error=err,
                error_message=err,
                next_run_at=next_run,
                locked_by=None,
                locked_at=None,
                lease_expires_at=None,
                updated_at=now,
            )
            return

        final_status, _ = status_for_exception(job.tier or "basic")
        AIJobModel.objects.filter(job_id=job_id).update(
            status=final_status,
            last_error=err,
            error_message=err,
            locked_by=None,
            locked_at=None,
            lease_expires_at=None,
            updated_at=now,
        )
