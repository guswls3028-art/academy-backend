from __future__ import annotations

from typing import Any, Callable

from apps.shared.contracts.ai_job import AIJob
from apps.shared.contracts.ai_result import AIResult


ProgressFn = Callable[..., None]


def run_matchup_public_cleanup(
    *,
    job: AIJob,
    payload: dict[str, Any],
    tenant_id: str | None,
    record_progress: ProgressFn,
) -> AIResult:
    """Create official/public cleaned matchup problem images in the AI worker."""
    from apps.domains.matchup.models import MatchupDocument
    from apps.domains.matchup.services import (
        clean_document_public_images,
        mark_document_public_cleanup_failed,
    )

    document_id = int(payload.get("document_id") or job.source_id or 0)
    if document_id <= 0:
        return AIResult.failed(job.id, "document_id missing")
    if not tenant_id:
        return AIResult.failed(job.id, "tenant_id missing")

    try:
        doc = MatchupDocument.objects.get(id=document_id, tenant_id=int(tenant_id))
    except MatchupDocument.DoesNotExist:
        return AIResult.failed(job.id, "matchup document not found")

    actor = None
    actor_id = payload.get("actor_id")
    if actor_id:
        try:
            from django.contrib.auth import get_user_model

            User = get_user_model()
            actor = User.objects.filter(id=int(actor_id)).first()
        except Exception:
            actor = None

    def _progress(done: int, total: int) -> None:
        total = max(1, int(total or 1))
        done = min(total, max(0, int(done or 0)))
        percent = 10 + int(done / total * 85)
        record_progress(
            job.id,
            "public_cleanup",
            min(98, percent),
            step_index=1,
            step_total=2,
            step_name_display="공개본 정리",
            step_percent=int(done / total * 100),
            tenant_id=tenant_id,
        )

    record_progress(
        job.id,
        "public_cleanup",
        5,
        step_index=1,
        step_total=2,
        step_name_display="공개본 정리",
        step_percent=0,
        tenant_id=tenant_id,
    )
    try:
        result = clean_document_public_images(
            doc,
            force=bool(payload.get("force")),
            actor=actor,
            job_id=job.id,
            on_progress=_progress,
        )
    except Exception as exc:
        mark_document_public_cleanup_failed(
            doc,
            job_id=job.id,
            error=str(exc) or "공개용 이미지 정리 실패",
        )
        return AIResult.failed(job.id, str(exc) or "public cleanup failed")
    record_progress(
        job.id,
        "done",
        100,
        step_index=2,
        step_total=2,
        step_name_display="완료",
        step_percent=100,
        tenant_id=tenant_id,
    )
    return AIResult.done(job.id, result)
