# apps/domains/ai/views_internal.py
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.db import transaction
from django.conf import settings

from apps.domains.ai.models import AIJobModel
from apps.shared.contracts.ai_job import AIJob


def _auth_worker(request):
    token = request.headers.get("X-Worker-Token")
    return token and token == settings.INTERNAL_WORKER_TOKEN


@require_GET
def next_ai_job(request):
    """
    Worker → API
    GET /api/v1/internal/ai/job/next/
    """
    if not _auth_worker(request):
        return JsonResponse({"detail": "unauthorized"}, status=401)

    with transaction.atomic():
        job = (
            AIJobModel.objects
            .select_for_update(skip_locked=True)
            .filter(status="PENDING")
            .order_by("created_at")
            .first()
        )

        if not job:
            return JsonResponse({"job": None})

        job.status = "RUNNING"
        job.save(update_fields=["status", "updated_at"])

    ai_job = AIJob(
        id=job.job_id,
        type=job.job_type,
        payload=job.payload,
        tenant_id=None,
        source_domain=None,
        source_id=None,
    )

    return JsonResponse({"job": ai_job.to_dict()})
