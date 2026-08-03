from __future__ import annotations

import logging

from django.db import transaction

from apps.domains.results.models import WrongNotePDF
from apps.domains.results.services.wrong_note_pdf_service import (
    WrongNotePDFEmptyError,
    WrongNotePDFLimitError,
    delete_wrong_note_pdf_object,
    generate_and_store_wrong_note_pdf,
    wrong_note_pdf_storage_key,
)
from apps.shared.contracts.ai_job import AIJob
from apps.shared.contracts.ai_result import AIResult

logger = logging.getLogger(__name__)

_SOURCE_DOMAIN = "results_wrong_note_pdf"
_GENERIC_ERROR = "오답노트를 만들지 못했습니다. 잠시 후 다시 시도해 주세요."


def _failed_result(
    *,
    ai_job: AIJob,
    pdf_job: WrongNotePDF,
    message: str,
    file_path: str = "",
) -> AIResult:
    return AIResult.done(
        ai_job.id,
        {
            "outcome": WrongNotePDF.Status.FAILED,
            "wrong_note_pdf_job_id": int(pdf_job.id),
            "error_message": message,
            "file_path": file_path,
        },
    )


def handle_wrong_note_pdf_generation_job(ai_job: AIJob) -> AIResult:
    """Generate a wrong-note PDF/HWPX on the tools worker and return its envelope."""
    source_id = str(ai_job.source_id or "")
    payload_id = str((ai_job.payload or {}).get("wrong_note_pdf_job_id") or "")
    if ai_job.source_domain != _SOURCE_DOMAIN or not source_id or payload_id != source_id:
        return AIResult.failed(ai_job.id, "Invalid wrong-note PDF job scope.")

    try:
        pdf_job_id = int(source_id)
    except (TypeError, ValueError):
        return AIResult.failed(ai_job.id, "Invalid wrong-note PDF job id.")
    try:
        tenant_id = int(str(ai_job.tenant_id or ""))
    except (TypeError, ValueError):
        return AIResult.failed(ai_job.id, "Invalid wrong-note PDF tenant scope.")

    try:
        with transaction.atomic():
            pdf_job = (
                WrongNotePDF.objects.select_for_update()
                .select_related("enrollment__tenant")
                .get(id=pdf_job_id, enrollment__tenant_id=tenant_id)
            )
            tenant = pdf_job.enrollment.tenant
            if pdf_job.status == WrongNotePDF.Status.DONE:
                return AIResult.done(
                    ai_job.id,
                    {
                        "outcome": WrongNotePDF.Status.DONE,
                        "wrong_note_pdf_job_id": int(pdf_job.id),
                        "file_path": str(pdf_job.file_path or ""),
                    },
                )
            if pdf_job.status == WrongNotePDF.Status.FAILED:
                return _failed_result(
                    ai_job=ai_job,
                    pdf_job=pdf_job,
                    message=str(pdf_job.error_message or _GENERIC_ERROR),
                    file_path=str(pdf_job.file_path or ""),
                )
            pdf_job.status = WrongNotePDF.Status.RUNNING
            pdf_job.error_message = ""
            pdf_job.save(update_fields=["status", "error_message", "updated_at"])
    except WrongNotePDF.DoesNotExist:
        return AIResult.failed(ai_job.id, "Wrong-note PDF job not found.")

    file_key = wrong_note_pdf_storage_key(job=pdf_job, tenant=tenant)
    try:
        stored_key = generate_and_store_wrong_note_pdf(
            job=pdf_job,
            enrollment=pdf_job.enrollment,
            tenant=tenant,
        )
        return AIResult.done(
            ai_job.id,
            {
                "outcome": WrongNotePDF.Status.DONE,
                "wrong_note_pdf_job_id": int(pdf_job.id),
                "file_path": stored_key,
            },
        )
    except (WrongNotePDFEmptyError, WrongNotePDFLimitError) as exc:
        cleanup_succeeded = delete_wrong_note_pdf_object(file_key)
        return _failed_result(
            ai_job=ai_job,
            pdf_job=pdf_job,
            message=str(exc),
            file_path="" if cleanup_succeeded else file_key,
        )
    except Exception:
        cleanup_succeeded = delete_wrong_note_pdf_object(file_key)
        logger.exception(
            "wrong-note PDF tools-worker generation failed",
            extra={
                "ai_job_id": ai_job.id,
                "wrong_note_pdf_job_id": int(pdf_job.id),
                "tenant_id": int(tenant.id),
            },
        )
        return _failed_result(
            ai_job=ai_job,
            pdf_job=pdf_job,
            message=_GENERIC_ERROR,
            file_path="" if cleanup_succeeded else file_key,
        )
