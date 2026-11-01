"""Rebuild the current result from preserved attempt facts and score snapshots."""
from __future__ import annotations

import math
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.core.exceptions import ValidationError
from apps.domains.results.models import ExamAttempt, Result, ResultFact, ResultItem
from apps.support.omr.score_shape import get_exam_score_shape


def _score_float(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError, OverflowError):
        return math.nan


def resolve_result_snapshot_from_attempt(
    *,
    result: Result,
    attempt: ExamAttempt,
    exam,
) -> tuple[list[ResultFact], float, float, object]:
    attempt_facts = ResultFact.objects.current().filter(
        target_type="exam",
        target_id=result.target_id,
        enrollment_id=result.enrollment_id,
        attempt_id=attempt.id,
    )
    ordered_facts = list(attempt_facts.order_by("id"))

    score_shape = get_exam_score_shape(exam)
    attempt_meta = attempt.meta if isinstance(attempt.meta, dict) else {}
    final_snapshot = (
        attempt_meta.get("final_result_snapshot")
        if isinstance(attempt_meta.get("final_result_snapshot"), dict)
        else {}
    )
    initial_snapshot = (
        attempt_meta.get("initial_snapshot")
        if isinstance(attempt_meta.get("initial_snapshot"), dict)
        else {}
    )

    terminal_total = next(
        (
            (source, value)
            for source, value in (
                ("meta", attempt_meta.get("total_score")),
                ("final", final_snapshot.get("total_score")),
                ("initial", initial_snapshot.get("total_score")),
            )
            if value is not None
        ),
        None,
    )
    if not ordered_facts and terminal_total is None:
        raise ValidationError(
            {
                "detail": "no score state for this attempt; cannot rebuild snapshot",
                "code": "INVALID",
            }
        )

    question_facts: dict[int, ResultFact] = {}
    for fact in ordered_facts:
        if int(fact.question_id) > 0:
            question_facts[int(fact.question_id)] = fact

    if terminal_total is not None:
        terminal_source, terminal_value = terminal_total
        total_score = _score_float(terminal_value)
        objective_value = {
            "meta": attempt_meta.get("objective_score"),
            "final": final_snapshot.get("objective_score"),
            "initial": initial_snapshot.get("objective_score"),
        }[terminal_source]

        if objective_value is None:
            objective_events: list[tuple[int, float]] = []
            for fact in ordered_facts:
                question_id = int(fact.question_id)
                meta = fact.meta if isinstance(fact.meta, dict) else {}
                snapshot = meta.get("result_snapshot")
                if question_id == 0 and isinstance(snapshot, dict):
                    if snapshot.get("objective_score") is not None:
                        objective_events.append(
                            (fact.id, _score_float(snapshot["objective_score"]))
                        )
                elif question_id == 0 and fact.source == "manual_objective":
                    objective_events.append(
                        (
                            fact.id,
                            _score_float(meta.get("objective_score", fact.score)),
                        )
                    )

            choice_facts = [
                fact
                for fact in question_facts.values()
                if score_shape.question_kind(int(fact.question_id)) == "choice"
            ]
            if choice_facts:
                objective_events.append(
                    (
                        max(fact.id for fact in choice_facts),
                        sum(_score_float(fact.score) for fact in choice_facts),
                    )
                )
            if objective_events:
                objective_value = max(objective_events, key=lambda event: event[0])[1]

        if objective_value is None and terminal_source == "meta":
            objective_value = final_snapshot.get("objective_score")
        if objective_value is None:
            objective_value = initial_snapshot.get("objective_score")
        if objective_value is None:
            initial_source = str(initial_snapshot.get("source") or "")
            objective_value = (
                total_score
                if initial_source
                in {"admin_manual_objective", "submission_sync", "omr"}
                else 0.0
            )
        objective_score = _score_float(objective_value)
    else:
        total_score = 0.0
        objective_score = 0.0
        subjective_score = _score_float(attempt_meta.get("subjective_score"))
        total_is_explicit = False
        subjective_is_explicit = attempt_meta.get("subjective_score") is not None

        replay_question_facts: dict[int, ResultFact] = {}
        for fact in ordered_facts:
            question_id = int(fact.question_id)
            source = str(fact.source or "")
            score = _score_float(fact.score)
            meta = fact.meta if isinstance(fact.meta, dict) else {}
            snapshot = meta.get("result_snapshot")
            if question_id == 0 and isinstance(snapshot, dict) and {
                "total_score",
                "objective_score",
            }.issubset(snapshot):
                total_score = _score_float(snapshot["total_score"])
                objective_score = _score_float(snapshot["objective_score"])
                total_is_explicit = True
                if source == "manual_subjective":
                    subjective_score = _score_float(
                        meta.get("subjective_score", score)
                    )
                    subjective_is_explicit = True
                elif source == "manual_total":
                    subjective_is_explicit = False
                continue

            if question_id > 0:
                replay_question_facts[question_id] = fact
                choice_facts = [
                    item
                    for item in replay_question_facts.values()
                    if score_shape.question_kind(int(item.question_id)) == "choice"
                ]
                essay_facts = [
                    item
                    for item in replay_question_facts.values()
                    if score_shape.question_kind(int(item.question_id)) == "essay"
                ]
                unknown_facts = [
                    item
                    for item in replay_question_facts.values()
                    if score_shape.question_kind(int(item.question_id)) is None
                ]
                if unknown_facts:
                    total_score = sum(
                        _score_float(item.score)
                        for item in replay_question_facts.values()
                    )
                    total_is_explicit = False
                else:
                    if choice_facts:
                        objective_score = sum(
                            _score_float(item.score) for item in choice_facts
                        )
                    if essay_facts:
                        subjective_score = sum(
                            _score_float(item.score) for item in essay_facts
                        )
                        subjective_is_explicit = True
                    total_score = objective_score + (
                        subjective_score if subjective_is_explicit else 0.0
                    )
                    total_is_explicit = False
            elif source == "manual_objective":
                objective_score = _score_float(meta.get("objective_score", score))
                if subjective_is_explicit:
                    total_score = objective_score + subjective_score
                    total_is_explicit = False
                elif not total_is_explicit:
                    total_score = objective_score
            elif source == "manual_subjective":
                subjective_score = _score_float(meta.get("subjective_score", score))
                subjective_is_explicit = True
                total_score = objective_score + subjective_score
                total_is_explicit = False
            elif source == "manual_total":
                total_score = score
                total_is_explicit = True
                subjective_is_explicit = False

    submitted_at_value = (
        final_snapshot.get("submitted_at")
        or initial_snapshot.get("submitted_at")
    )
    submitted_at = (
        parse_datetime(submitted_at_value)
        if isinstance(submitted_at_value, str)
        else submitted_at_value
    ) or attempt.updated_at or attempt.created_at or timezone.now()
    return (
        list(question_facts.values()),
        total_score,
        objective_score,
        submitted_at,
    )

def apply_result_snapshot(
    *,
    result: Result,
    attempt_id: int,
    question_facts: list[ResultFact],
    total_score: float,
    objective_score: float,
    current_max_score: float,
    submitted_at,
) -> Result:
    ResultItem.objects.select_for_update().filter(result=result).delete()
    for fact in question_facts:
        ResultItem.objects.create(
            result=result,
            question_id=int(fact.question_id),
            answer=str(fact.answer or ""),
            is_correct=bool(fact.is_correct),
            include_in_wrong_note=bool(
                (fact.meta or {}).get("include_in_wrong_note")
                if isinstance(fact.meta, dict)
                else False
            ),
            score=float(fact.score or 0.0),
            max_score=float(fact.max_score or 0.0),
            source=str(fact.source or ""),
        )

    result.attempt_id = int(attempt_id)
    result.total_score = float(total_score)
    result.objective_score = float(objective_score)
    result.max_score = float(current_max_score)
    result.submitted_at = submitted_at
    result.save(
        update_fields=[
            "attempt_id",
            "total_score",
            "objective_score",
            "max_score",
            "submitted_at",
            "updated_at",
        ]
    )

    return result
