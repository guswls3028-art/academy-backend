# PATH: apps/api/v1/internal/ai/views.py
from __future__ import annotations

import logging
from typing import Any

from django.db import transaction
from django.utils import timezone

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.domains.submissions.services.ai_result_router import apply_ai_result_for_submission
from apps.domains.results.services.grading_service import grade_submission

logger = logging.getLogger(__name__)


def _http_400(detail: str, **extra: Any) -> Response:
    body = {"detail": detail}
    body.update(extra)
    return Response(body, status=400)


def _get_job_model():
    try:
        from apps.domains.ai.models import AIJobModel  # type: ignore
        return AIJobModel
    except Exception as e:
        raise RuntimeError(
            "AIJobModel import failed. HTTP worker requires DB-backed AI job storage (AIJobModel)."
        ) from e


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def next_ai_job_view(request):
    """
    GET /api/v1/internal/ai/next/
    - HTTP worker polls this endpoint.
    - One PENDING job is atomically claimed -> PROCESSING.
    """
    AIJobModel = _get_job_model()

    with transaction.atomic():
        job = (
            AIJobModel.objects.select_for_update(skip_locked=True)
            .filter(status="PENDING")
            .order_by("id")
            .first()
        )

        if not job:
            return Response({"job": None}, status=200)

        job.status = "PROCESSING"
        if hasattr(job, "started_at"):
            job.started_at = timezone.now()
        job.save()

        return Response(
            {
                "job": {
                    "job_id": getattr(job, "job_id", None),
                    "type": getattr(job, "type", None),
                    "source_domain": getattr(job, "source_domain", None),
                    "source_id": getattr(job, "source_id", None),
                    "payload": getattr(job, "payload", None) or {},
                }
            },
            status=200,
        )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def submit_ai_result_view(request):
    """
    POST /api/v1/internal/ai/submit/
    - Worker submits AI output.
    - API applies to submission (meta/answers), and triggers grading if needed.
    - Idempotent: repeated submits are safe.
    """
    data = request.data if isinstance(request.data, dict) else {}

    job_id = data.get("job_id")
    submission_id = data.get("submission_id") or data.get("source_id")
    status_str = data.get("status") or data.get("result_status") or "DONE"
    result = data.get("result")
    error = data.get("error")

    if not submission_id:
        return _http_400("submission_id missing")

    try:
        submission_id_int = int(submission_id)
    except Exception:
        return _http_400("submission_id invalid", submission_id=submission_id)

    try:
        outcome = apply_ai_result_for_submission(
            submission_id=submission_id_int,
            status=str(status_str),
            result=result if isinstance(result, dict) else (result or {}),
            error=str(error) if error else None,
        )
    except Exception:
        logger.exception("apply_ai_result_for_submission failed (submission_id=%s)", submission_id_int)
        return Response({"detail": "apply_ai_result failed"}, status=500)

    graded_result_id = None
    if outcome.should_grade:
        try:
            r = grade_submission(submission_id_int)
            graded_result_id = getattr(r, "id", None)
        except Exception:
            logger.exception("grade_submission failed after ai result (submission_id=%s)", submission_id_int)
            return Response(
                {
                    "ok": True,
                    "submission_id": submission_id_int,
                    "should_grade": True,
                    "graded": False,
                    "detail": "grading failed",
                },
                status=200,
            )

    if job_id:
        try:
            AIJobModel = _get_job_model()
            with transaction.atomic():
                job = (
                    AIJobModel.objects.select_for_update(skip_locked=True)
                    .filter(job_id=str(job_id))
                    .order_by("-id")
                    .first()
                )
                if job:
                    st = str(status_str).upper()
                    job.status = "FAILED" if st == "FAILED" else "DONE"
                    if hasattr(job, "finished_at"):
                        job.finished_at = timezone.now()
                    if hasattr(job, "error_message"):
                        job.error_message = str(error) if error else ""
                    job.save()
        except Exception:
            logger.exception("AIJobModel status update failed (job_id=%s)", job_id)

    return Response(
        {
            "ok": True,
            "submission_id": submission_id_int,
            "should_grade": bool(outcome.should_grade),
            "graded": bool(outcome.should_grade),
            "exam_result_id": graded_result_id,
            "detail": outcome.detail,
        },
        status=200,
    )
