from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_CHOICE_LABELS = {"1", "2", "3", "4", "5"}


@dataclass(frozen=True)
class OMRSheetShape:
    total_questions: int
    choice_count: int
    essay_count: int
    source: str


def resolve_omr_sheet_shape(*, sheet, exam=None) -> OMRSheetShape:
    """
    Resolve the rendered OMR shape for a sheet.

    `Sheet.total_questions` is the full exam size. The AI detector only scans
    objective bubbles, so the objective/essay boundary must be explicit. Older
    sheets did not persist that boundary; for them, infer it from AnswerKey
    values where choice answers are 1~5 or comma-separated 1~5.
    """
    total = int(getattr(sheet, "total_questions", 0) or 0)
    stored_choice = int(getattr(sheet, "choice_count", 0) or 0)
    stored_essay = int(getattr(sheet, "essay_count", 0) or 0)

    if stored_choice or stored_essay:
        choice_count = stored_choice or max(0, total - stored_essay)
        essay_count = stored_essay or max(0, total - choice_count)
        return _normalize(
            total=total,
            choice_count=choice_count,
            essay_count=essay_count,
            source="sheet",
        )

    inferred = _infer_choice_count_from_answer_key(sheet=sheet, exam=exam)
    if inferred is not None:
        return _normalize(
            total=total,
            choice_count=inferred,
            essay_count=max(0, total - inferred),
            source="answer_key",
        )

    return _normalize(
        total=total,
        choice_count=total,
        essay_count=0,
        source="legacy_total",
    )


def _normalize(
    *,
    total: int,
    choice_count: int,
    essay_count: int,
    source: str,
) -> OMRSheetShape:
    total = max(0, int(total or 0))
    choice_count = max(0, int(choice_count or 0))
    essay_count = max(0, int(essay_count or 0))
    if total and choice_count + essay_count > total:
        essay_count = max(0, total - choice_count)
    if total and choice_count == 0 and essay_count == 0:
        choice_count = total
    return OMRSheetShape(
        total_questions=total,
        choice_count=choice_count,
        essay_count=essay_count,
        source=source,
    )


def _infer_choice_count_from_answer_key(*, sheet, exam=None) -> int | None:
    try:
        from apps.domains.exams.models import AnswerKey, ExamQuestion
        from apps.domains.exams.services.template_resolver import resolve_template_exam

        template_exam = resolve_template_exam(exam or sheet.exam)
        answer_key = AnswerKey.objects.filter(exam=template_exam).first()
        if not answer_key or not isinstance(answer_key.answers, dict):
            return None

        questions = list(
            ExamQuestion.objects.filter(sheet=sheet)
            .only("id", "number")
            .order_by("number")
        )
        if not questions:
            return None

        last_choice_number = 0
        seen_non_choice = False
        for q in questions:
            raw = answer_key.answers.get(str(q.id))
            if raw is None or str(raw).strip() == "":
                return None
            if _is_choice_answer(raw):
                if seen_non_choice:
                    return None
                last_choice_number = int(q.number)
                continue
            seen_non_choice = True

        if seen_non_choice:
            return last_choice_number
    except Exception:
        return None
    return None


def _is_choice_answer(value: Any) -> bool:
    from apps.domains.results.services.answer_matching import correct_answer_sets

    answer_sets = correct_answer_sets(value)
    if not answer_sets:
        return False
    return all(
        token in _CHOICE_LABELS
        for answer_set in answer_sets
        for token in answer_set
    )
