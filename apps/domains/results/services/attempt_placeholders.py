"""Shared markers for first-attempt placeholders that a paper OMR scan can complete.

A placeholder is an ``ExamAttempt`` with ``submission_id=0`` that represents a
teacher decision made before the student's scan was matched:

* ``awaiting_omr_objective`` — the grading grid saved essay scores for a mixed
  exam while the objective OMR result was still missing. The result is held
  as pending (never published, ranked or used for clinic) until the scan
  attaches and the objective score is computed.
* ``absence_resume`` — a teacher explicitly confirmed, in OMR review, that a
  specific scan belongs to a student previously marked absent. Only that scan
  may replace the absence placeholder.
"""
from __future__ import annotations

from typing import Any

from django.db.models import Q

AWAITING_OMR_OBJECTIVE_META_KEY = "awaiting_omr_objective"
ABSENCE_RESUME_META_KEY = "absence_resume"


def is_awaiting_omr_objective(attempt: Any) -> bool:
    if attempt is None or int(getattr(attempt, "submission_id", 0) or 0) != 0:
        return False
    meta = attempt.meta if isinstance(getattr(attempt, "meta", None), dict) else {}
    return (
        isinstance(meta.get(AWAITING_OMR_OBJECTIVE_META_KEY), dict)
        and meta.get("status") != "NOT_SUBMITTED"
    )


def absence_fact_q() -> Q:
    """Zero-score absence decisions written by every absence entry path."""
    zero_absence = Q(question_id=0, answer="", score=0)
    return zero_absence & (
        Q(source="manual_not_submitted")
        | Q(source__in=("manual_grid", "excel_import"), meta__status="NOT_SUBMITTED")
    )


def is_absence_resume_confirmed_for_submission(
    *, meta: dict[str, Any], submission_id: int,
) -> bool:
    resume = meta.get(ABSENCE_RESUME_META_KEY)
    if not isinstance(resume, dict):
        return False
    try:
        return int(resume.get("confirmed_submission_id") or 0) == int(submission_id) > 0
    except (TypeError, ValueError):
        return False


def _pure_absence_placeholder(*, exam_id: int, enrollment_id: int, lock: bool):
    """Return the unused first-attempt absence placeholder, or ``None``."""
    from apps.domains.results.models import ExamAttempt, Result, ResultFact, ResultItem

    attempts = ExamAttempt.objects.filter(
        exam_id=int(exam_id), enrollment_id=int(enrollment_id),
    )
    if lock:
        attempts = attempts.select_for_update()
    placeholder = attempts.filter(
        submission_id=0, attempt_index=1, is_representative=True,
    ).first()
    if placeholder is None or attempts.exclude(pk=placeholder.pk).exists():
        return None
    meta = placeholder.meta if isinstance(placeholder.meta, dict) else {}
    if meta.get("status") != "NOT_SUBMITTED":
        return None
    result = Result.objects.filter(
        target_type="exam", target_id=int(exam_id),
        enrollment_id=int(enrollment_id), attempt_id=placeholder.id,
    ).first()
    if result is None or result.total_score != 0 or result.objective_score != 0:
        return None
    if ResultItem.objects.filter(result=result).exists():
        return None
    facts = ResultFact.objects.filter(attempt=placeholder)
    if facts.exclude(absence_fact_q()).exists():
        return None
    # Mirrors ExamAttemptService.attach_absence_placeholder_for_submission.
    if result.submitted_at is not None and not facts.exists():
        return None
    return placeholder


def absence_blocks_confirmed_scan(*, submission: Any) -> bool:
    """True when an explicit teacher resume would let this scan be graded."""
    from apps.domains.results.models import ExamAttempt

    if not getattr(submission, "enrollment_id", None):
        return False
    own = ExamAttempt.objects.filter(
        exam_id=int(submission.target_id),
        enrollment_id=int(submission.enrollment_id),
        submission_id=int(submission.id),
    ).first()
    if own is not None:
        return isinstance(own.meta, dict) and own.meta.get("status") == "NOT_SUBMITTED"
    return _pure_absence_placeholder(
        exam_id=int(submission.target_id),
        enrollment_id=int(submission.enrollment_id),
        lock=False,
    ) is not None


def record_absence_resume_for_confirmed_scan(
    *, submission: Any, user_id: int | None,
) -> str | None:
    """Record a teacher's explicit decision to grade this scan despite absence.

    Returns ``"own_attempt"`` when the scan's own attempt was marked absent
    (the status is cleared so the scan is regraded), ``"placeholder"`` when a
    pre-scan absence placeholder may now be replaced by this exact scan, or
    ``None`` when nothing was absent. Lock order follows grading:
    Submission -> Enrollment -> Result -> Attempt.
    """
    from django.utils import timezone

    from apps.domains.results.models import ExamAttempt
    from apps.support.results.grading_dependencies import (
        lock_exam_submission_regrade_state,
    )

    if not getattr(submission, "enrollment_id", None):
        return None
    exam_id = int(submission.target_id)
    enrollment_id = int(submission.enrollment_id)
    _locked, _result, own_absent = lock_exam_submission_regrade_state(
        submission_id=int(submission.id),
        exam_id=exam_id,
        tenant_id=int(submission.tenant_id),
    )
    now = timezone.now()
    if own_absent:
        attempt = ExamAttempt.objects.select_for_update().get(
            exam_id=exam_id, enrollment_id=enrollment_id,
            submission_id=int(submission.id),
        )
        meta = dict(attempt.meta or {})
        meta.pop("status", None)
        meta["absence_resumptions"] = [
            *list(meta.get("absence_resumptions") or []),
            {
                "at": now.isoformat(),
                "by_user_id": user_id,
                "submission_id": int(submission.id),
                "via": "omr_review",
            },
        ]
        attempt.meta = meta
        attempt.save(update_fields=["meta", "updated_at"])
        return "own_attempt"
    placeholder = _pure_absence_placeholder(
        exam_id=exam_id, enrollment_id=enrollment_id, lock=True,
    )
    if placeholder is None:
        return None
    meta = dict(placeholder.meta or {})
    meta[ABSENCE_RESUME_META_KEY] = {
        "confirmed_submission_id": int(submission.id),
        "confirmed_at": now.isoformat(),
        "by_user_id": user_id,
    }
    placeholder.meta = meta
    placeholder.save(update_fields=["meta", "updated_at"])
    return "placeholder"
