from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound

from apps.domains.results.models import ScoreEditDraft


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


def score_edit_lease_payload(*, client_id: str, changes: list) -> dict:
    return {"client_id": client_id, "changes": changes}


def require_score_edit_lease(request, *, session_id: int, exam_id: int | None = None):
    tenant = getattr(request, "tenant", None)
    if not tenant:
        raise ScoreEditLeaseConflict()

    from apps.domains.lectures.models import Session

    session = (
        Session.objects.select_for_update().filter(
            id=int(session_id),
            lecture__tenant=tenant,
        )
        .prefetch_related("exams")
        .first()
    )
    if session is None:
        raise NotFound({"detail": "session not found", "code": "NOT_FOUND"})
    if exam_id is not None and not session.exams.filter(id=int(exam_id)).exists():
        raise ScoreEditLeaseConflict()

    draft = ScoreEditDraft.objects.filter(
        session_id=int(session_id),
        tenant_id=tenant.id,
        editor_user_id=request.user.id,
        updated_at__gte=timezone.now() - EDIT_LEASE_TTL,
    ).first()
    if draft is None:
        raise ScoreEditLeaseConflict()
    stored_client_id, _ = score_edit_payload_parts(draft.payload)
    if stored_client_id is None or stored_client_id != score_edit_client_id(request):
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
