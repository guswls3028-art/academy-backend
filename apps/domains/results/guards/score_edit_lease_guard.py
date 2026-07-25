from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound

from apps.domains.results.models import ScoreEditDraft
from apps.support.results.progress_read_dependencies import (
    lock_score_edit_scope_for_exam,
    lock_score_edit_scope_for_session,
)


EDIT_LEASE_TTL = timedelta(minutes=2)
EDIT_CLIENT_HEADER = "HTTP_X_SCORE_EDITOR_CLIENT"
EDIT_SESSION_HEADER = "HTTP_X_SCORE_SESSION_ID"


class ScoreEditLeaseConflict(APIException):
    status_code = 409
    default_detail = {
        "detail": "이 차시는 다른 화면에서 수정 중입니다.",
        "code": "SCORE_EDIT_LOCKED",
    }
    default_code = "SCORE_EDIT_LOCKED"


class ScoreEditLeaseStale(APIException):
    status_code = 409
    default_detail = {
        "detail": "시험 제출 또는 다른 안전한 작업으로 서버 점수가 변경되었습니다.",
        "code": "SCORE_EDIT_STALE",
    }
    default_code = "SCORE_EDIT_STALE"


def score_edit_client_id(request) -> str:
    value = str(request.META.get(EDIT_CLIENT_HEADER, "") or "").strip()
    return value[:128] or f"legacy-user-{request.user.id}"


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


def _active_drafts(*, scope_ids: list[int], tenant_id: int):
    return ScoreEditDraft.objects.filter(
        session_id__in=scope_ids,
        tenant_id=int(tenant_id),
        updated_at__gte=timezone.now() - EDIT_LEASE_TTL,
    )


def _same_editor(draft, *, user_id: int, client_id: str) -> bool:
    stored_client_id, _ = score_edit_payload_parts(draft.payload)
    return (
        int(draft.editor_user_id) == int(user_id)
        and stored_client_id in (None, client_id)
    )


def require_score_edit_lease(request, *, session_id: int, exam_id: int | None = None):
    tenant = getattr(request, "tenant", None)
    if not tenant:
        raise ScoreEditLeaseConflict()

    try:
        session, scope_ids = lock_score_edit_scope_for_session(
            session_id=int(session_id),
            tenant=tenant,
        )
    except NotFound:
        raise NotFound({"detail": "session not found", "code": "NOT_FOUND"}) from None
    if exam_id is not None and not session.exams.filter(id=int(exam_id)).exists():
        raise ScoreEditLeaseConflict()

    client_id = score_edit_client_id(request)
    drafts = list(
        _active_drafts(scope_ids=scope_ids, tenant_id=tenant.id)
        .select_for_update()
        .order_by("session_id", "id")
    )
    draft = None
    for candidate in drafts:
        if (
            int(candidate.session_id) == int(session_id)
            and int(candidate.editor_user_id) == int(request.user.id)
        ):
            stored_client_id, _ = score_edit_payload_parts(candidate.payload)
            if stored_client_id in (None, client_id):
                draft = candidate
        if score_edit_payload_is_invalidated(candidate.payload):
            continue
        if not _same_editor(
            candidate,
            user_id=request.user.id,
            client_id=client_id,
        ):
            raise ScoreEditLeaseConflict()
    if draft is None:
        raise ScoreEditLeaseConflict()
    if score_edit_payload_is_invalidated(draft.payload):
        raise ScoreEditLeaseStale()
    stored_client_id, _ = score_edit_payload_parts(draft.payload)
    if stored_client_id is None or stored_client_id != client_id:
        raise ScoreEditLeaseConflict()
    return session


def require_score_edit_lease_from_headers(request, *, exam_id: int | None = None):
    raw_session_id = str(request.META.get(EDIT_SESSION_HEADER, "") or "").strip()
    try:
        session_id = int(raw_session_id)
    except (TypeError, ValueError):
        raise ScoreEditLeaseConflict() from None
    return require_score_edit_lease(
        request,
        session_id=session_id,
        exam_id=exam_id,
    )


def require_score_edit_scope_available_for_exam(*, exam, tenant) -> list[int]:
    """Fence administrative score writers against active manual editors."""
    scope_ids = lock_score_edit_scope_for_exam(
        exam_id=int(exam.id),
        tenant=tenant,
    )
    active = list(
        _active_drafts(scope_ids=scope_ids, tenant_id=tenant.id)
        .select_for_update()
        .order_by("session_id", "id")
    )
    if any(not score_edit_payload_is_invalidated(draft.payload) for draft in active):
        raise ScoreEditLeaseConflict()
    return scope_ids


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
        _active_drafts(scope_ids=scope_ids, tenant_id=tenant.id)
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
