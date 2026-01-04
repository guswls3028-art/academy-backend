# apps/api/v1/internal/ai/views.py

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt

import redis

from apps.shared.contracts.ai_job import AIJob
from apps.shared.contracts.ai_result import AIResult
from apps.domains.submissions.services.ai_result_mapper import apply_ai_result
from apps.domains.results.tasks.grading_tasks import grade_submission_task


def _redis() -> redis.Redis:
    # settings.REDIS_URL 이미 있음
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


def _auth_or_401(request) -> Optional[JsonResponse]:
    token = request.headers.get("X-Worker-Token")
    if not token or token != getattr(settings, "INTERNAL_WORKER_TOKEN", None):
        return JsonResponse({"detail": "unauthorized"}, status=401)
    return None


QUEUE_KEY = "ai:jobs"  # Redis List


@csrf_exempt
@require_http_methods(["GET"])
def next_ai_job_view(request):
    unauth = _auth_or_401(request)
    if unauth:
        return unauth

    r = _redis()

    # rpop: 가장 단순한 MVP (at-least-once는 아님. 운영은 streams 추천)
    raw = r.rpop(QUEUE_KEY)
    if not raw:
        return JsonResponse({"job": None}, status=200)

    try:
        job = AIJob.from_json(raw)
        return JsonResponse({"job": job.to_dict()}, status=200)
    except Exception:
        # 파싱 실패하면 버림(혹은 DLQ로)
        return JsonResponse({"job": None}, status=200)


@csrf_exempt
@require_http_methods(["POST"])
def submit_ai_result_view(request):
    unauth = _auth_or_401(request)
    if unauth:
        return unauth

    try:
        body = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"detail": "invalid json"}, status=400)

    # body: AIResult dict + (submission_id는 result.apply 때 필요)
    try:
        ai_result = AIResult.from_dict(body)
    except Exception:
        return JsonResponse({"detail": "invalid ai_result"}, status=400)

    # ✅ result 반영 (submissions)
    # 여기서는 ai_result.result 안에 submission_id가 들어오도록 worker가 같이 보내거나,
    # job.source_id를 함께 보내는 형태로 맞추면 됨.
    payload: Dict[str, Any] = dict(ai_result.result or {})
    submission_id = body.get("submission_id") or payload.get("submission_id")
    if submission_id:
        payload["submission_id"] = int(submission_id)

    returned_submission_id = apply_ai_result(payload)

    # ✅ 채점 enqueue
    if returned_submission_id:
        grade_submission_task.delay(int(returned_submission_id))

    return JsonResponse({"ok": True}, status=200)
