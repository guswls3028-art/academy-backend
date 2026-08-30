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


def score_edit_payload_active_cell(payload) -> dict | None:
    if not isinstance(payload, dict):
        return None
    active_cell = payload.get("active_cell")
    return normalize_homework_active_cell(active_cell)


def normalize_homework_active_cell(value) -> dict | None:
    if not isinstance(value, dict) or value.get("type") != "homework":
        return None
    enrollment_id = value.get("enrollmentId")
    homework_id = value.get("homeworkId")
    if (
        isinstance(enrollment_id, bool)
        or isinstance(homework_id, bool)
        or not isinstance(enrollment_id, int)
        or not isinstance(homework_id, int)
        or enrollment_id <= 0
        or homework_id <= 0
    ):
        return None
    return {
        "type": "homework",
        "enrollmentId": enrollment_id,
        "homeworkId": homework_id,
    }


def score_edit_active_homework_key(value) -> tuple[int, int] | None:
    active_cell = normalize_homework_active_cell(value)
    if active_cell is None:
        return None
    return (active_cell["enrollmentId"], active_cell["homeworkId"])


def score_edit_payload_is_invalidated(payload) -> bool:
    return bool(isinstance(payload, dict) and payload.get("invalidated"))


def score_edit_homework_keys(changes: list) -> frozenset[tuple[int, int]] | None:
    """Return homework cell keys, or None when the draft needs an exclusive lease."""
    keys: set[tuple[int, int]] = set()
    for change in changes:
        if not isinstance(change, dict) or change.get("type") != "homework":
            return None
        enrollment_id = change.get("enrollmentId")
        homework_id = change.get("homeworkId")
        if (
            isinstance(enrollment_id, bool)
            or isinstance(homework_id, bool)
            or not isinstance(enrollment_id, int)
            or not isinstance(homework_id, int)
            or enrollment_id <= 0
            or homework_id <= 0
        ):
            return None
        keys.add((enrollment_id, homework_id))
    return frozenset(keys)


def score_edit_changes_conflict(left_changes: list, right_changes: list) -> bool:
    """Empty drafts coexist; disjoint homework cells coexist; all else is exclusive."""
    if not left_changes or not right_changes:
        return False
    left_keys = score_edit_homework_keys(left_changes)
    right_keys = score_edit_homework_keys(right_changes)
    if left_keys is None or right_keys is None:
        return True
    return bool(left_keys & right_keys)


def score_edit_changes_are_exclusive(changes: list) -> bool:
    return bool(changes) and score_edit_homework_keys(changes) is None


def score_edit_lease_payload(
    *,
    client_id: str | None,
    changes: list,
    active_cell: dict | None = None,
    invalidated: bool = False,
    invalidated_reason: str | None = None,
) -> dict:
    payload = {
        "client_id": client_id,
        "changes": changes,
        "active_cell": normalize_homework_active_cell(active_cell),
    }
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
            active_cell=score_edit_payload_active_cell(draft.payload),
            invalidated=True,
            invalidated_reason=reason,
        )
        draft.save(update_fields=["payload", "updated_at"])
        invalidated_count += 1
    return invalidated_count
