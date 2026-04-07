# PATH: apps/domains/ai/views/internal_ai_job_view.py
from __future__ import annotations

from typing import Any, Dict, Optional

from django.conf import settings
from rest_framework import status
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.shared.contracts.ai_job import AIJob as AIJobContract
from apps.domains.ai.queueing.db_queue import DBJobQueue
from apps.domains.ai.services.status_resolver import status_for_exception
from academy.adapters.db.django import repositories_ai as ai_repo


class IsInternalWorker(BasePermission):
    """Validate X-Worker-Token header against INTERNAL_WORKER_TOKEN setting."""

    def has_permission(self, request, view):
        expected = str(getattr(settings, "INTERNAL_WORKER_TOKEN", "") or "")
        if not expected:
            return False
        token = (
            request.headers.get("X-Worker-Token")
            or request.META.get("HTTP_X_WORKER_TOKEN")
            or ""
        )
        return str(token) == expected


def _worker_id(request) -> str:
    return request.headers.get("X-Worker-Id") or request.META.get("HTTP_X_WORKER_ID") or "ai-worker"


class InternalAIJobNextView(APIView):
    """
    GET /api/v1/internal/ai/job/next/
    response: { "job": {...} | null }
    """

    permission_classes = [IsInternalWorker]

    def get(self, request):
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

    permission_classes = [IsInternalWorker]

    def post(self, request):
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

        job = ai_repo.get_job_model_by_job_id(str(job_id))
        if not job:
            return Response({"detail": "job not found"}, status=status.HTTP_404_NOT_FOUND)

        if ai_repo.result_exists_for_job(job):
            return Response({"ok": True, "detail": "duplicate_ignored"}, status=status.HTTP_200_OK)

        q = DBJobQueue()
        tier = (job.tier or "basic").lower()

        # 🔐 중복 콜백 방어: IntegrityError 발생 시 중복으로 처리
        from django.db import IntegrityError as DjIntegrityError

        if status_in == "FAILED" and tier in ("lite", "basic"):
            _, flags = status_for_exception(tier)
            payload_to_store = dict(result or {})
            payload_to_store["flags"] = {**(payload_to_store.get("flags") or {}), **flags}
            try:
                ai_repo.result_create(job, payload_to_store)
            except DjIntegrityError:
                return Response({"ok": True, "detail": "duplicate_ignored"}, status=status.HTTP_200_OK)
            q.mark_done(job_id=job.job_id)
            return Response({"ok": True, "detail": "done_lite_basic_no_fail"}, status=status.HTTP_200_OK)

        try:
            ai_repo.result_create(job, result or None)
        except DjIntegrityError:
            return Response({"ok": True, "detail": "duplicate_ignored"}, status=status.HTTP_200_OK)

        if status_in == "FAILED":
            q.mark_failed(job_id=job.job_id, error=error or "failed", retryable=True)
            return Response({"ok": True, "detail": "failed_recorded"}, status=status.HTTP_200_OK)

        q.mark_done(job_id=job.job_id)

        # ── 도메인 콜백 (채점 등 후속 처리) ──
        # SQS 워커가 직접 콜백하지 못한 경우(HTTP 워커 경로)에도 여기서 트리거.
        try:
            from apps.domains.ai.callbacks import dispatch_ai_result_to_domain
            dispatch_ai_result_to_domain(
                job_id=str(job.job_id),
                status=status_in,
                result_payload=result if isinstance(result, dict) else {},
                error=error or None,
                source_domain=job.source_domain,
                source_id=job.source_id,
                tier=tier,
            )
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "InternalAIJobResultView: domain callback failed (non-fatal) job_id=%s",
                job.job_id,
            )

        return Response({"ok": True, "detail": "done"}, status=status.HTTP_200_OK)
