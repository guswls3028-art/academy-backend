"""Public cross-domain entry points owned by the results domain."""

from __future__ import annotations


def require_score_edit_lease(request, *, session_id: int, exam_id: int | None = None):
    """Enforce the results domain's score-edit lease contract."""
    from .guards.score_edit_lease_guard import require_score_edit_lease as _require

    return _require(request, session_id=session_id, exam_id=exam_id)


def require_homework_score_edit_lease(
    request,
    *,
    session_id: int,
    enrollment_id: int,
    homework_id: int,
):
    """Enforce cell-scoped homework edit ownership."""
    from .guards.score_edit_lease_guard import require_homework_score_edit_lease as _require

    return _require(
        request,
        session_id=session_id,
        enrollment_id=enrollment_id,
        homework_id=homework_id,
    )
