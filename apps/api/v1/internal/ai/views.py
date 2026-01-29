# PATH: apps/api/v1/internal/ai/views.py
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt

import redis

from apps.shared.contracts.ai_job import AIJob
from apps.shared.contracts.ai_result import AIResult

from apps.domains.submissions.services.ai_result_router import apply_ai_result_for_submission
from apps.domains.results.tasks.grading_tasks import grade_submission_task


logger = logging.getLogger(__name__)


def _redis() -> redis.Redis:
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


def _auth_or_401(request) -> Optional[JsonResponse]:
    token = request.headers.get("X-Worker-Token")
    if not token or token != getattr(settings, "INTERNAL_WORKER_TOKEN", None):
        return JsonResponse({"detail": "unauthorized"}, status=401)
    return None


QUEUE_KEY = "ai:jobs"          # Redis List
DEAD_KEY = "ai:jobs:dead"      # Redis List (dead-letter)


@csrf_exempt
@require_http_methods(["GET"])
def next_ai_job_view(request):
    unauth = _auth_or_401(request)
    if unauth:
        return unauth

    r = _redis()
    raw = r.rpop(QUEUE_KEY)
    if not raw:
        return JsonResponse({"job": None}, status=200)

    try:
        job = AIJob.from_json(raw)
        return JsonResponse({"job": job.to_dict()}, status=200)

    except Exception:
        # ✅ PATCH: 절대 유실 금지 → dead-letter로 이동
        logger.exception("AIJob parsing failed. Moving to dead-letter.")
        r.lpush(DEAD_KEY, raw)
        return JsonResponse({"job": None}, status=200)


@csrf_exempt
@require_http_methods(["POST"])
def submit_ai_result_view(request):
    """
    Worker → API 콜백 (STEP 1 확정)
    """
    unauth = _auth_or_401(request)
    if unauth:
        return unauth

    try:
        body = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"detail": "invalid json"}, status=400)

    submission_id = body.get("submission_id")
    if not submission_id:
        return JsonResponse({"detail": "submission_id required"}, status=400)

    status = body.get("status") or "DONE"
    result = body.get("result") if isinstance(body.get("result"), dict) else (body.get("result") or None)
    error = body.get("error")

    outcome = apply_ai_result_for_submission(
        submission_id=int(submission_id),
        status=str(status),
        result=result if isinstance(result, dict) else None,
        error=str(error) if error else None,
    )

    if outcome.returned_submission_id and outcome.should_grade:
        grade_submission_task.delay(int(outcome.returned_submission_id))

    return JsonResponse(
        {"ok": True, "detail": outcome.detail},
        status=200,
    )
