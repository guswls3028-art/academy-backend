# PATH: apps/domains/ai/views/internal_ai_job_view.py
from __future__ import annotations

from typing import Any, Dict, Optional

from django.conf import settings
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.shared.contracts.ai_job import AIJob as AIJobContract
from apps.domains.ai.models import AIJobModel, AIResultModel
from apps.domains.ai.queueing.db_queue import DBJobQueue
from apps.domains.ai.services.status_resolver import status_for_exception


def _get_worker_token_secret() -> str:
    v = getattr(settings, "INTERNAL_WORKER_TOKEN", None)
    return str(v or "")


def _require_worker_auth(request) -> Optional[Response]:
    expected = _get_worker_token_secret()
    if not expected:
        return Response(
            {"detail": "INTERNAL_WORKER_TOKEN not configured"},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    token = request.headers.get("X-Worker-Token") or request.META.get("HTTP_X_WORKER_TOKEN") or ""
    if str(token) != str(expected):
        return Response({"detail": "Unauthorized worker"}, status=status.HTTP_401_UNAUTHORIZED)
    return None


def _worker_id(request) -> str:
    return request.headers.get("X-Worker-Id") or request.META.get("HTTP_X_WORKER_ID") or "ai-worker"


class InternalAIJobNextView(APIView):
    """
    GET /api/v1/internal/ai/job/next/
    response: { "job": {...} | null }
    """

    permission_classes = [AllowAny]

    def get(self, request):
        auth = _require_worker_auth(request)
        if auth:
            return auth

        q = DBJobQueue()
        claimed = q.claim(worker_id=_worker_id(request))
        if not claimed:
            return Response({"job": None}, status=status.HTTP_200_OK)

        job = AIJobContract.from_dict(
            {
                "id": claimed.job_id,
                "type": claimed.job_type,
                "payload": claimed.payload,
                "tenant_id": claimed.tenant_id,
                "source_domain": claimed.source_domain,
                "source_id": claimed.source_id,
            }
        )
        return Response({"job": job.to_dict()}, status=status.HTTP_200_OK)


class InternalAIJobResultView(APIView):
    """
    POST /api/v1/internal/ai/job/result/
    payload:
      {
        "job_id": "...",
        "submission_id": 123,      # optional legacy
        "status": "DONE|FAILED",
        "result": {...} | null,
        "error": "..." | null
      }
    """

    permission_classes = [AllowAny]

    def post(self, request):
        auth = _require_worker_auth(request)
        if auth:
            return auth

        data: Dict[str, Any] = request.data if isinstance(request.data, dict) else {}

        job_id = data.get("job_id")
        if not job_id:
            return Response({"detail": "job_id required"}, status=status.HTTP_400_BAD_REQUEST)

        status_in = str(data.get("status") or "DONE").upper().strip()
        if status_in not in ("DONE", "FAILED"):
            return Response({"detail": "status must be DONE or FAILED"}, status=status.HTTP_400_BAD_REQUEST)

        result = data.get("result")
        if result is not None and not isinstance(result, dict):
            return Response({"detail": "result must be object or null"}, status=status.HTTP_400_BAD_REQUEST)

        error = str(data.get("error") or "")

        job = AIJobModel.objects.filter(job_id=str(job_id)).first()
        if not job:
            return Response({"detail": "job not found"}, status=status.HTTP_404_NOT_FOUND)

        # idempotent: if result already stored, ignore duplicates
        if AIResultModel.objects.filter(job=job).exists():
            return Response({"ok": True, "detail": "duplicate_ignored"}, status=status.HTTP_200_OK)

        q = DBJobQueue()
        tier = (job.tier or "basic").lower()

        # Lite/Basic 실패 없음: 워커가 FAILED를 보내도 DONE + review_candidate로 저장
        if status_in == "FAILED" and tier in ("lite", "basic"):
            _, flags = status_for_exception(tier)
            payload_to_store = dict(result or {})
            payload_to_store["flags"] = {**(payload_to_store.get("flags") or {}), **flags}
            AIResultModel.objects.create(job=job, payload=payload_to_store)
            q.mark_done(job_id=job.job_id)
            return Response({"ok": True, "detail": "done_lite_basic_no_fail"}, status=status.HTTP_200_OK)

        # store fact
        AIResultModel.objects.create(job=job, payload=(result or None))

        if status_in == "FAILED":
            q.mark_failed(job_id=job.job_id, error=error or "failed", retryable=True)
            return Response({"ok": True, "detail": "failed_recorded"}, status=status.HTTP_200_OK)

        q.mark_done(job_id=job.job_id)
        return Response({"ok": True, "detail": "done"}, status=status.HTTP_200_OK)
