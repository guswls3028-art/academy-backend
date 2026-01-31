from __future__ import annotations

import json
from typing import Optional
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt

from apps.shared.contracts.ai_job import AIJob
from apps.domains.ai.queue import DBJobQueue


def _auth_or_401(request) -> Optional[JsonResponse]:
    token = request.headers.get("X-Worker-Token")
    if not token or token != settings.INTERNAL_WORKER_TOKEN:
        return JsonResponse({"detail": "unauthorized"}, status=401)
    return None


@csrf_exempt
@require_http_methods(["GET"])
def next_ai_job_view(request):
    unauth = _auth_or_401(request)
    if unauth:
        return unauth

    worker_id = request.headers.get("X-Worker-Id", "ai-worker")

    queue = DBJobQueue(
        worker_id=worker_id,
        visibility_seconds=60,
    )

    job = queue.claim_next()
    if not job:
        return JsonResponse({"job": None}, status=200)

    contract = AIJob.new(
        type=job.job_type,
        payload=job.payload,
        source_id=job.job_id,
    )

    return JsonResponse({"job": contract.to_dict()}, status=200)
