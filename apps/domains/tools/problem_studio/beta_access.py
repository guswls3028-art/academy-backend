from __future__ import annotations

from typing import Any

from django.db import transaction
from django.db.models import Count

from apps.core.models import Tenant
from apps.domains.tools.problem_studio.models import ProblemStudioBetaRun


BETA_FREE_RUN_LIMIT = 3


class ProblemStudioBetaLimitReached(Exception):
    pass


def beta_access_snapshot(*, tenant: Any) -> dict[str, Any]:
    counts = {
        row["status"]: row["count"]
        for row in (
            ProblemStudioBetaRun.objects.filter(tenant=tenant)
            .values("status")
            .annotate(count=Count("id"))
        )
    }
    completed = int(counts.get(ProblemStudioBetaRun.Status.COMPLETED, 0))
    reserved = int(counts.get(ProblemStudioBetaRun.Status.RESERVED, 0))
    remaining = max(0, BETA_FREE_RUN_LIMIT - completed - reserved)
    return {
        "label": "Beta",
        "free_run_limit": BETA_FREE_RUN_LIMIT,
        "completed_runs": completed,
        "reserved_runs": reserved,
        "remaining_runs": remaining,
        "can_start": remaining > 0,
        "review_required": True,
    }


@transaction.atomic
def reserve_beta_run(*, tenant: Any, user: Any) -> ProblemStudioBetaRun:
    Tenant.objects.select_for_update().get(pk=tenant.pk)
    active = ProblemStudioBetaRun.objects.filter(
        tenant=tenant,
        status__in=[
            ProblemStudioBetaRun.Status.RESERVED,
            ProblemStudioBetaRun.Status.COMPLETED,
        ],
    ).count()
    if active >= BETA_FREE_RUN_LIMIT:
        raise ProblemStudioBetaLimitReached
    return ProblemStudioBetaRun.objects.create(
        tenant=tenant,
        requested_by=user,
        status=ProblemStudioBetaRun.Status.RESERVED,
    )


def bind_beta_run(*, run: ProblemStudioBetaRun, job_id: str) -> None:
    ProblemStudioBetaRun.objects.filter(
        pk=run.pk,
        status=ProblemStudioBetaRun.Status.RESERVED,
    ).update(job_id=str(job_id))


def release_beta_run(*, run_id: str, reason: str) -> None:
    ProblemStudioBetaRun.objects.filter(
        pk=run_id,
        status=ProblemStudioBetaRun.Status.RESERVED,
    ).update(
        status=ProblemStudioBetaRun.Status.RELEASED,
        release_reason=str(reason or "system_failure")[:240],
    )


def beta_run_id_from_job_payload(payload: Any) -> str:
    payload = payload if isinstance(payload, dict) else {}
    studio_payload = payload.get("problem_studio_payload")
    if not isinstance(studio_payload, dict):
        studio_payload = {}
    beta = studio_payload.get("beta")
    if not isinstance(beta, dict):
        beta = payload.get("beta") if isinstance(payload.get("beta"), dict) else {}
    return str(beta.get("run_id") or "")


@transaction.atomic
def settle_beta_run(
    *,
    run_id: str,
    job_id: str,
    terminal_status: str,
    error: str = "",
) -> None:
    if not run_id:
        return
    run = ProblemStudioBetaRun.objects.select_for_update().filter(pk=run_id).first()
    if run is None or run.status != ProblemStudioBetaRun.Status.RESERVED:
        return
    run.job_id = str(job_id)
    if terminal_status == "DONE":
        run.status = ProblemStudioBetaRun.Status.COMPLETED
        run.release_reason = ""
    else:
        run.status = ProblemStudioBetaRun.Status.RELEASED
        run.release_reason = str(error or terminal_status or "system_failure")[:240]
    run.save(update_fields=["job_id", "status", "release_reason", "updated_at"])
