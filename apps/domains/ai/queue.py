from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from django.db.models import Q

from apps.domains.ai.models import AIJobModel


class DBJobQueue:
    """
    DB-backed Job Queue (SQS-style semantics)
    """

    def __init__(self, *, worker_id: str, visibility_seconds: int = 60):
        self.worker_id = worker_id
        self.visibility_seconds = visibility_seconds

    @transaction.atomic
    def claim_next(self) -> AIJobModel | None:
        now = timezone.now()
        expired = now - timedelta(seconds=self.visibility_seconds)

        job = (
            AIJobModel.objects
            .select_for_update(skip_locked=True)
            .filter(
                Q(status="PENDING") |
                Q(status="RUNNING", locked_at__lt=expired)
            )
            .order_by("created_at")
            .first()
        )

        if not job:
            return None

        if job.retry_count >= job.max_retries:
            job.status = "FAILED"
            job.error_message = "max retries exceeded"
            job.locked_at = None
            job.locked_by = None
            job.save()
            return None

        job.status = "RUNNING"
        job.retry_count += 1
        job.locked_by = self.worker_id
        job.locked_at = now
        job.save()

        return job

    @transaction.atomic
    def mark_done(self, job: AIJobModel):
        job.status = "DONE"
        job.locked_by = None
        job.locked_at = None
        job.save(update_fields=["status", "locked_by", "locked_at", "updated_at"])

    @transaction.atomic
    def mark_failed(self, job: AIJobModel, error: str):
        job.status = "FAILED"
        job.error_message = error[:2000]
        job.locked_by = None
        job.locked_at = None
        job.save(update_fields=["status", "error_message", "locked_by", "locked_at"])
