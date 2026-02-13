"""
AIJobRepository - IAIJobRepository 구현체

Django ORM을 사용하여 AI Job 상태 업데이트.
Worker는 모델을 직접 부르지 않고 repo.mark_processing(), repo.complete_job() 등만 호출.
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from src.application.ports.ai_repository import IAIJobRepository
from apps.domains.ai.models import AIJobModel
from apps.domains.ai.models import AIResultModel

logger = logging.getLogger(__name__)


class AIJobRepository(IAIJobRepository):
    """IAIJobRepository 구현 (Django ORM)"""

    @transaction.atomic
    def mark_processing(self, job_id: str) -> bool:
        job = AIJobModel.objects.select_for_update().filter(job_id=job_id).first()
        if not job:
            return False

        if job.status == "RUNNING":
            return True

        if job.status != "PENDING":
            logger.warning(
                "Cannot mark AI job %s as RUNNING: status=%s",
                job_id,
                job.status,
            )
            return False

        job.status = "RUNNING"
        job.locked_at = timezone.now()
        job.locked_by = "sqs-worker"

        job.save(update_fields=["status", "locked_at", "locked_by"])
        return True

    @transaction.atomic
    def complete_job(
        self,
        job_id: str,
        result_payload: dict,
    ) -> tuple[bool, str]:
        job = AIJobModel.objects.select_for_update().filter(job_id=job_id).first()
        if not job:
            return False, "not_found"

        if job.status == "DONE":
            return True, "idempotent"

        job.status = "DONE"
        job.locked_at = None
        job.locked_by = None

        result, _ = AIResultModel.objects.get_or_create(
            job=job,
            defaults={"payload": result_payload},
        )
        if result.payload != result_payload:
            result.payload = result_payload
            result.save(update_fields=["payload"])

        job.save(update_fields=["status", "locked_at", "locked_by"])
        return True, "ok"

    @transaction.atomic
    def fail_job(self, job_id: str, error_message: str) -> tuple[bool, str]:
        job = AIJobModel.objects.select_for_update().filter(job_id=job_id).first()
        if not job:
            return False, "not_found"

        if job.status == "FAILED":
            return True, "idempotent"

        job.status = "FAILED"
        job.error_message = str(error_message)[:2000]
        job.last_error = str(error_message)[:2000]
        job.locked_at = None
        job.locked_by = None

        job.save(update_fields=["status", "error_message", "last_error", "locked_at", "locked_by"])
        return True, "ok"
