# PATH: apps/api/v1/internal/ai/views.py
from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.domains.submissions.services.ai_result_router import (
    apply_ai_result_for_submission,
)
from apps.domains.results.services.grading_service import grade_submission

logger = logging.getLogger(__name__)


def _http_400(detail: str, **extra: Any) -> Response:
    body = {"detail": detail}
    body.update(extra)
    return Response(body, status=400)


def _unauthorized() -> Response:
    return Response({"detail": "unauthorized"}, status=401)


def _check_worker_auth(request) -> bool:
    token = request.headers.get("X-Worker-Token")
    expected = getattr(settings, "INTERNAL_WORKER_TOKEN", None)
    return bool(expected and token and token == expected)


def _get_job_model():
    from apps.domains.ai.models import AIJobModel
    return AIJobModel


@api_view(["GET"])
@authentication_classes([])          # 🔥 DRF JWT 완전 비활성화
@permission_classes([AllowAny])      # 🔥 권한 체크 스킵
def next_ai_job_view(request):
    """
    GET /api/v1/internal/ai/next/
    """
    if not _check_worker_auth(request):
        return _unauthorized()

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
                    "job_id": job.job_id,
                    "type": job.job_type,
                    "source_domain": job.source_domain,
                    "source_id": job.source_id,
                    "payload": job.payload or {},
                }
            },
            status=200,
        )


@api_view(["POST"])
@authentication_classes([])          # 🔥 DRF JWT 완전 비활성화
@permission_classes([AllowAny])      # 🔥 권한 체크 스킵
def submit_ai_result_view(request):
    """
    POST /api/v1/internal/ai/submit/
    """
    if not _check_worker_auth(request):
        return _unauthorized()

    data = request.data if isinstance(request.data, dict) else {}

    job_id = data.get("job_id")
    submission_id = data.get("submission_id") or data.get("source_id")
    status_str = data.get("status") or "DONE"
    result = data.get("result")
    error = data.get("error")

    if not submission_id:
        return _http_400("submission_id missing")

    try:
        submission_id_int = int(submission_id)
    except Exception:
        return _http_400("submission_id invalid", submission_id=submission_id)

    outcome = apply_ai_result_for_submission(
        submission_id=submission_id_int,
        status=str(status_str),
        result=result if isinstance(result, dict) else {},
        error=str(error) if error else None,
    )

    graded_result_id = None
    if outcome.should_grade:
        r = grade_submission(submission_id_int)
        graded_result_id = getattr(r, "id", None)

    if job_id:
        AIJobModel = _get_job_model()
        with transaction.atomic():
            job = (
                AIJobModel.objects.select_for_update(skip_locked=True)
                .filter(job_id=str(job_id))
                .first()
            )
            if job:
                job.status = "FAILED" if str(status_str).upper() == "FAILED" else "DONE"
                if hasattr(job, "finished_at"):
                    job.finished_at = timezone.now()
                job.save()

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
