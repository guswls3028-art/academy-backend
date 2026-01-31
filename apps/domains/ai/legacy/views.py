# PATH: apps/api/v1/internal/ai/views.py
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone

import redis  # legacy 유지

from apps.shared.contracts.ai_job import AIJob

from apps.domains.ai.models import AIJobModel, AIResultModel
from apps.domains.ai.queueing.db_queue import DBJobQueue

from apps.domains.submissions.services.ai_result_router import apply_ai_result_for_submission
from apps.domains.results.tasks.grading_tasks import grade_submission_task


logger = logging.getLogger(__name__)


# -------------------------
# Legacy Redis (삭제 금지)
# -------------------------
def _redis() -> redis.Redis:
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


QUEUE_KEY = "ai:jobs"          # Redis List
DEAD_KEY = "ai:jobs:dead"      # Redis List (dead-letter)


# -------------------------
# Auth
# -------------------------
def _auth_or_401(request) -> Optional[JsonResponse]:
    token = request.headers.get("X-Worker-Token")
    if not token or token != getattr(settings, "INTERNAL_WORKER_TOKEN", None):
        return JsonResponse({"detail": "unauthorized"}, status=401)
    return None


def _queue_backend() -> str:
    """
    기본은 DB (운영 정석)
    필요 시 settings.AI_QUEUE_BACKEND="redis"로 legacy 사용 가능
    """
    return str(getattr(settings, "AI_QUEUE_BACKEND", "db")).lower().strip() or "db"


# -------------------------
# GET /api/v1/internal/ai/job/next/
# -------------------------
@csrf_exempt
@require_http_methods(["GET"])
def next_ai_job_view(request):
    unauth = _auth_or_401(request)
    if unauth:
        return unauth

    backend = _queue_backend()

    # ==================================================
    # ✅ 운영 기본: DBQueue claim (SQS 스타일 lease)
    # ==================================================
    if backend == "db":
        worker_id = request.headers.get("X-Worker-Id") or request.META.get("REMOTE_ADDR") or "worker"
        q = DBJobQueue()
        claimed = q.claim(worker_id=str(worker_id))
        if not claimed:
            return JsonResponse({"job": None}, status=200)

        job = AIJob.new(
            type=claimed.job_type,
            payload=claimed.payload,
            tenant_id=claimed.tenant_id,
            source_domain=claimed.source_domain,
            source_id=claimed.source_id,
        )
        # ✅ contracts 고정: job.id를 claimed.job_id로 덮어써야 하지만 AIJob.new는 새 UUID 생성 가능성.
        # 그래서 from_dict로 강제 구성: AIJob의 실제 구조는 contracts에 따르며, 여기서는 dict 기반으로 안정화.
        # (contracts 수정 금지 조건 충족)
        try:
            job = AIJob.from_dict({
                "id": claimed.job_id,
                "type": claimed.job_type,
                "payload": claimed.payload,
                "tenant_id": claimed.tenant_id,
                "source_domain": claimed.source_domain,
                "source_id": claimed.source_id,
            })
        except Exception:
            # 최후 방어: 최소 필드만 반환
            return JsonResponse({"job": {"id": claimed.job_id, "type": claimed.job_type, "payload": claimed.payload}}, status=200)

        return JsonResponse({"job": job.to_dict()}, status=200)

    # ==================================================
    # Legacy: Redis pop (삭제 금지)
    # ==================================================
    r = _redis()
    raw = r.rpop(QUEUE_KEY)
    if not raw:
        return JsonResponse({"job": None}, status=200)

    try:
        job = AIJob.from_json(raw)
        return JsonResponse({"job": job.to_dict()}, status=200)

    except Exception:
        logger.exception("AIJob parsing failed. Moving to dead-letter.")
        r.lpush(DEAD_KEY, raw)
        return JsonResponse({"job": None}, status=200)


# -------------------------
# POST /api/v1/internal/ai/job/result/
# -------------------------
@csrf_exempt
@require_http_methods(["POST"])
def submit_ai_result_view(request):
    """
    Worker → API 콜백 (운영 정석)
    - job_id 기반 idempotent
    - submission_id는 선택(optional)로만 처리
    - 결과 중복 제출 방지: ai_result one-to-one 기반 업서트
    """
    unauth = _auth_or_401(request)
    if unauth:
        return unauth

    try:
        body = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"detail": "invalid json"}, status=400)

    job_id = body.get("job_id")
    submission_id = body.get("submission_id")  # legacy/optional
    status = str(body.get("status") or "DONE").upper()
    result = body.get("result") if isinstance(body.get("result"), dict) else None
    error = body.get("error")
    error_str = str(error) if error else ""

    # ✅ 최소 요구: job_id (정석)
    if not job_id:
        # 호환: submission_id만 온 경우(구형 워커), job row에서 source_id 매칭 시도
        if submission_id:
            job = AIJobModel.objects.filter(source_id=str(submission_id)).order_by("-created_at").first()
            if job:
                job_id = job.job_id
        if not job_id:
            return JsonResponse({"detail": "job_id required"}, status=400)

    job = AIJobModel.objects.filter(job_id=str(job_id)).first()
    if not job:
        return JsonResponse({"detail": "job not found"}, status=404)

    # ==================================================
    # ✅ idempotent: 이미 결과가 저장돼 있으면 그대로 OK
    # ==================================================
    existing = AIResultModel.objects.filter(job=job).first()
    if existing:
        # 상태만 보정(이미 DONE인데 다시 FAILED 같은 건 무시)
        return JsonResponse({"ok": True, "detail": "duplicate_ignored"}, status=200)

    # ==================================================
    # 결과 저장 (fact)
    # ==================================================
    AIResultModel.objects.create(
        job=job,
        payload=result,
    )

    # ==================================================
    # 상태 반영 + retry 정책
    # ==================================================
    q = DBJobQueue()

    if status == "FAILED":
        # job을 retryable로 보고 backoff 스케줄링 (attempt_count/max_attempts로 제어)
        q.mark_failed(job_id=job.job_id, error=error_str or "failed", retryable=True)
        return JsonResponse({"ok": True, "detail": "failed_recorded"}, status=200)

    # DONE
    q.mark_done(job_id=job.job_id)

    # ==================================================
    # 도메인 라우팅: submission 기반 작업일 때만 적용
    # (시험지 생성/숙제 검사 등 submission_id 없는 타입도 정상 완료)
    # ==================================================
    if submission_id:
        outcome = apply_ai_result_for_submission(
            submission_id=int(submission_id),
            status="DONE",
            result=result if isinstance(result, dict) else None,
            error=None,
        )
        if outcome.returned_submission_id and outcome.should_grade:
            grade_submission_task.delay(int(outcome.returned_submission_id))

        return JsonResponse({"ok": True, "detail": outcome.detail}, status=200)

    return JsonResponse({"ok": True, "detail": "done"}, status=200)
