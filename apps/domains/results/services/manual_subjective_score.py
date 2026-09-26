from __future__ import annotations

from collections.abc import Iterable

from django.db.models import Max, Q

from apps.domains.results.models import Result, ResultFact, ResultItem


def latest_subjective_grading_facts(
    results: Iterable[Result],
    *,
    essay_question_ids: set[int],
    include_total: bool = False,
) -> dict[int, ResultFact]:
    """Latest teacher input per attempt; totals count only for completion/mode."""
    by_attempt = {int(row.attempt_id): row for row in results if row.attempt_id}
    if not by_attempt:
        return {}
    facts = ResultFact.objects.filter(
        target_type="exam",
        attempt_id__in=by_attempt,
        target_id__in={row.target_id for row in by_attempt.values()},
        enrollment_id__in={row.enrollment_id for row in by_attempt.values()},
    ).filter(
        Q(
            source__in=("manual_subjective", "manual_total") if include_total else ("manual_subjective",),
            question_id=0,
        )
        | Q(source__in=("manual", "manual_grid"), question_id__in=essay_question_ids)
    )
    # Batch progress polls need the latest event, not every historical payload.
    latest_ids = facts.order_by().values(
        "attempt_id", "target_id", "enrollment_id",
    ).annotate(latest_id=Max("id")).values("latest_id")
    latest = {}
    for fact in ResultFact.objects.filter(id__in=latest_ids):
        row = by_attempt[int(fact.attempt_id)]
        if fact.target_id == row.target_id and fact.enrollment_id == row.enrollment_id:
            latest.setdefault(int(fact.attempt_id), fact)
    return latest


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

    essay_question_ids = {
        int(question_id)
        for question_id, kind in score_shape.question_kind_by_id.items()
        if kind == "essay"
    }
    fact = latest_subjective_grading_facts(
        [result], essay_question_ids=essay_question_ids,
    ).get(int(attempt.id))
    if fact and fact.source == "manual_subjective":
        return max(0.0, safe_float(fact.score))
    manual_item_score = 0.0
    has_manual_essay_item = False
    for item in ResultItem.objects.filter(
        result=result,
        source__in=["manual", "manual_grid"],
    ):
        if score_shape.question_kind(int(item.question_id)) == "essay":
            manual_item_score += safe_float(item.score)
            has_manual_essay_item = True
    if has_manual_essay_item:
        return max(0.0, manual_item_score)

    meta_score = manual_subjective_score_from_attempt_meta(attempt)
    if meta_score is not None:
        return max(0.0, meta_score)

    return 0.0
