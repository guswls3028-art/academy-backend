from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from apps.domains.results.models import ScoreEditDraft
from apps.support.results.progress_read_dependencies import (
    lock_score_edit_scope_for_exam,
)


EDIT_LEASE_TTL = timedelta(minutes=2)


def score_edit_payload_parts(payload) -> tuple[str | None, list]:
    if isinstance(payload, dict):
        changes = payload.get("changes")
        return (
            str(payload.get("client_id") or "") or None,
            changes if isinstance(changes, list) else [],
        )
    return None, payload if isinstance(payload, list) else []


def score_edit_payload_is_invalidated(payload) -> bool:
    return bool(isinstance(payload, dict) and payload.get("invalidated"))


def score_edit_lease_payload(
    *,
    client_id: str | None,
    changes: list,
    invalidated: bool = False,
    invalidated_reason: str | None = None,
) -> dict:
    payload = {"client_id": client_id, "changes": changes}
    if invalidated:
        payload["invalidated"] = True
        payload["invalidated_reason"] = (
            invalidated_reason or "SCORE_UPDATED_EXTERNALLY"
        )
    return payload


def active_score_edit_drafts(*, scope_ids: list[int], tenant_id: int):
    return ScoreEditDraft.objects.filter(
        session_id__in=scope_ids,
        tenant_id=int(tenant_id),
        updated_at__gte=timezone.now() - EDIT_LEASE_TTL,
    )


def invalidate_score_edit_leases_for_exam(
    *,
    exam,
    tenant,
    reason: str = "SCORE_UPDATED_EXTERNALLY",
) -> int:
    """Let authoritative grading proceed while fencing stale manual writes."""
    scope_ids = lock_score_edit_scope_for_exam(
        exam_id=int(exam.id),
        tenant=tenant,
    )
    drafts = list(
        active_score_edit_drafts(scope_ids=scope_ids, tenant_id=tenant.id)
        .select_for_update()
        .order_by("session_id", "id")
    )
    invalidated_count = 0
    for draft in drafts:
        if score_edit_payload_is_invalidated(draft.payload):
            continue
        client_id, changes = score_edit_payload_parts(draft.payload)
        draft.payload = score_edit_lease_payload(
            client_id=client_id,
            changes=changes,
            invalidated=True,
            invalidated_reason=reason,
        )
        draft.save(update_fields=["payload", "updated_at"])
        invalidated_count += 1
    return invalidated_count
