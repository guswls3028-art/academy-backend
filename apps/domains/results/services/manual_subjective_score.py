from __future__ import annotations

from apps.domains.results.models import Result, ResultFact, ResultItem


def safe_float(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def manual_subjective_score_from_attempt_meta(attempt) -> float | None:
    meta = dict(attempt.meta or {}) if attempt and isinstance(attempt.meta, dict) else {}

    explicit_subjective = meta.get("subjective_score")
    if explicit_subjective is not None:
        return max(0.0, safe_float(explicit_subjective))

    placeholder = (
        meta.get("manual_score_placeholder")
        if isinstance(meta.get("manual_score_placeholder"), dict)
        else {}
    )
    previous_initial = (
        placeholder.get("previous_initial_snapshot")
        if isinstance(placeholder.get("previous_initial_snapshot"), dict)
        else {}
    )
    if str(previous_initial.get("source") or "") == "admin_manual_subjective":
        return max(0.0, safe_float(previous_initial.get("total_score")))

    initial = meta.get("initial_snapshot") if isinstance(meta.get("initial_snapshot"), dict) else {}
    if str(initial.get("source") or "") == "admin_manual_subjective":
        return max(0.0, safe_float(initial.get("total_score")))

    return None


def explicit_manual_subjective_score_for_result(
    *,
    result: Result | None,
    attempt,
    score_shape,
) -> float:
    if not result or not attempt:
        return 0.0

    manual_item_score = 0.0
    has_manual_essay_item = False
    for item in ResultItem.objects.filter(result=result, source="manual"):
        if score_shape.question_kind(int(item.question_id)) == "essay":
            manual_item_score += safe_float(item.score)
            has_manual_essay_item = True
    if has_manual_essay_item:
        return max(0.0, manual_item_score)

    fact = (
        ResultFact.objects
        .filter(
            target_type="exam",
            target_id=int(result.target_id),
            enrollment_id=int(result.enrollment_id),
            attempt_id=int(attempt.id),
            source="manual_subjective",
        )
        .order_by("-id")
        .first()
    )
    if fact:
        return max(0.0, safe_float(fact.score))

    meta_score = manual_subjective_score_from_attempt_meta(attempt)
    if meta_score is not None:
        return max(0.0, meta_score)

    return 0.0
