"""First-attempt score projection shared by reports, counseling, and statistics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Iterable

from django.utils.dateparse import parse_datetime

from apps.domains.results.models import ExamAttempt


@dataclass(frozen=True)
class InitialExamScore:
    total_score: float | None
    max_score: float | None
    not_submitted: bool
    attempt_id: int | None = None
    recorded_at: datetime | None = None


def _safe_score(value) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _safe_datetime(value, *, fallback: datetime | None = None) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        return parse_datetime(value.strip()) or fallback
    return fallback


def load_initial_exam_scores(
    *,
    exam_ids: Iterable[int],
    enrollment_ids: Iterable[int],
) -> dict[tuple[int, int], InitialExamScore]:
    """Load preserved first-attempt scores without consulting mutable Result rows."""
    normalized_exam_ids = sorted({int(value) for value in exam_ids})
    normalized_enrollment_ids = sorted({int(value) for value in enrollment_ids})
    if not normalized_exam_ids or not normalized_enrollment_ids:
        return {}

    states: dict[tuple[int, int], InitialExamScore] = {}
    attempts = ExamAttempt.objects.filter(
        exam_id__in=normalized_exam_ids,
        enrollment_id__in=normalized_enrollment_ids,
        attempt_index=1,
    ).only("exam_id", "enrollment_id", "meta", "created_at")
    for attempt in attempts:
        meta = attempt.meta if isinstance(attempt.meta, dict) else {}
        snapshot = (
            meta.get("initial_snapshot")
            if isinstance(meta.get("initial_snapshot"), dict)
            else {}
        )
        states[(int(attempt.exam_id), int(attempt.enrollment_id))] = InitialExamScore(
            total_score=_safe_score(snapshot.get("total_score"))
            if snapshot
            else _safe_score(meta.get("total_score")),
            max_score=_safe_score(snapshot.get("max_score"))
            if snapshot
            else _safe_score(meta.get("max_score")),
            not_submitted=meta.get("status") == "NOT_SUBMITTED",
            attempt_id=int(attempt.id),
            recorded_at=_safe_datetime(
                snapshot.get("submitted_at") if snapshot else None,
                fallback=attempt.created_at,
            ),
        )
    return states


def project_initial_exam_score(
    *,
    state: InitialExamScore | None,
    fallback_score,
    fallback_max_score,
    fallback_not_submitted: bool = False,
    fallback_recorded_at: datetime | None = None,
) -> InitialExamScore:
    """Prefer the preserved first attempt; use Result only for legacy missing snapshots."""
    if state is not None and state.not_submitted:
        return InitialExamScore(
            total_score=None,
            max_score=state.max_score or _safe_score(fallback_max_score),
            not_submitted=True,
            attempt_id=state.attempt_id,
            recorded_at=state.recorded_at or fallback_recorded_at,
        )
    if state is not None and state.total_score is not None:
        return InitialExamScore(
            total_score=state.total_score,
            max_score=state.max_score or _safe_score(fallback_max_score),
            not_submitted=False,
            attempt_id=state.attempt_id,
            recorded_at=state.recorded_at or fallback_recorded_at,
        )
    if fallback_not_submitted:
        return InitialExamScore(
            total_score=None,
            max_score=_safe_score(fallback_max_score),
            not_submitted=True,
            recorded_at=fallback_recorded_at,
        )
    return InitialExamScore(
        total_score=_safe_score(fallback_score),
        max_score=_safe_score(fallback_max_score),
        not_submitted=False,
        recorded_at=fallback_recorded_at,
    )
