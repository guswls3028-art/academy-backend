from __future__ import annotations

from decimal import Decimal
from typing import Any

from academy.domain.omr import OMRQuestionContract, OMRSheetContract
from apps.domains.assets.omr.services.meta_generator import build_objective_template_meta


_CHOICE_LABELS = {"1", "2", "3", "4", "5"}


def build_omr_sheet_contract(*, sheet, exam=None, n_choices: int = 5) -> OMRSheetContract:
    template_exam = _resolve_template_exam(exam or getattr(sheet, "exam", None))
    questions = _load_sheet_questions(sheet)
    total, choice_count, essay_count, source = _resolve_counts(
        sheet=sheet,
        template_exam=template_exam,
        questions=questions,
    )
    question_contracts = _build_question_contracts(
        questions=questions,
        total_questions=total,
        choice_count=choice_count,
        use_explicit_kinds=source == "question_types",
    )
    choice_numbers = [q.number for q in question_contracts if q.kind == "choice"]
    essay_numbers = [q.number for q in question_contracts if q.kind == "essay"]
    template_meta = build_objective_template_meta(
        question_count=choice_count,
        n_choices=n_choices,
        essay_count=essay_count,
        choice_question_numbers=choice_numbers,
        essay_question_numbers=essay_numbers,
    )
    return OMRSheetContract(
        sheet_id=getattr(sheet, "id", None),
        exam_id=getattr(getattr(sheet, "exam", None), "id", None),
        template_exam_id=getattr(template_exam, "id", None),
        total_questions=total,
        choice_count=choice_count,
        essay_count=essay_count,
        n_choices=n_choices,
        shape_source=source,
        questions=tuple(question_contracts),
        template_meta=template_meta,
    )


def _resolve_template_exam(exam):
    if exam is None:
        return None
    from apps.domains.exams.services.template_resolver import resolve_template_exam

    return resolve_template_exam(exam)


def _load_sheet_questions(sheet) -> list:
    from apps.domains.exams.models import ExamQuestion

    return list(
        ExamQuestion.objects.filter(sheet=sheet)
        .only("id", "number", "score", "question_kind")
        .order_by("number")
    )


def _resolve_counts(*, sheet, template_exam, questions: list) -> tuple[int, int, int, str]:
    max_question_number = max((int(getattr(q, "number", 0) or 0) for q in questions), default=0)
    total = max(int(getattr(sheet, "total_questions", 0) or 0), max_question_number)
    explicit_kinds = {
        int(question.number): question.question_kind
        for question in questions
        if getattr(question, "question_kind", None) in {"choice", "essay"}
    }
    if total > 0 and set(explicit_kinds) == set(range(1, total + 1)):
        choice_count = sum(kind == "choice" for kind in explicit_kinds.values())
        return total, choice_count, total - choice_count, "question_types"

    stored_choice = int(getattr(sheet, "choice_count", 0) or 0)
    stored_essay = int(getattr(sheet, "essay_count", 0) or 0)

    if stored_choice or stored_essay:
        choice_count = stored_choice
        essay_count = stored_essay
        if total == 0:
            total = choice_count + essay_count
        if choice_count and not essay_count:
            essay_count = max(0, total - choice_count)
        elif essay_count and not choice_count:
            choice_count = max(0, total - essay_count)
        total, choice_count, essay_count = _normalize_counts(
            total=total,
            choice_count=choice_count,
            essay_count=essay_count,
            prefer_unknown_as_essay=True,
        )
        return total, choice_count, essay_count, "sheet"

    inferred = _infer_choice_count_from_answer_key(
        sheet=sheet,
        template_exam=template_exam,
        questions=questions,
    )
    if inferred is not None:
        total, choice_count, essay_count = _normalize_counts(
            total=total,
            choice_count=inferred,
            essay_count=max(0, total - inferred),
            prefer_unknown_as_essay=True,
        )
        return total, choice_count, essay_count, "answer_key"

    total, choice_count, essay_count = _normalize_counts(
        total=total,
        choice_count=total,
        essay_count=0,
        prefer_unknown_as_essay=False,
    )
    return total, choice_count, essay_count, "legacy_total"


def _normalize_counts(
    *,
    total: int,
    choice_count: int,
    essay_count: int,
    prefer_unknown_as_essay: bool,
) -> tuple[int, int, int]:
    total = max(0, int(total or 0))
    choice_count = max(0, int(choice_count or 0))
    essay_count = max(0, int(essay_count or 0))

    if total == 0:
        total = choice_count + essay_count
    if total == 0:
        return 0, 0, 0

    if choice_count + essay_count > total:
        essay_count = max(0, total - choice_count)
    if choice_count + essay_count < total:
        remainder = total - (choice_count + essay_count)
        if prefer_unknown_as_essay:
            essay_count += remainder
        else:
            choice_count += remainder
    return total, choice_count, essay_count


def _infer_choice_count_from_answer_key(*, sheet, template_exam, questions: list) -> int | None:
    try:
        from apps.domains.exams.models import AnswerKey

        answer_key = AnswerKey.objects.filter(exam=template_exam).order_by("id").first()
        if not answer_key or not isinstance(answer_key.answers, dict) or not questions:
            return None

        last_choice_number = 0
        seen_non_choice = False
        for question in questions:
            raw = answer_key.answers.get(str(question.id))
            if raw is None or str(raw).strip() == "":
                return None
            if _is_choice_answer(raw):
                if seen_non_choice:
                    return None
                last_choice_number = int(question.number)
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
    return all(token in _CHOICE_LABELS for answer_set in answer_sets for token in answer_set)


def _build_question_contracts(
    *,
    questions: list,
    total_questions: int,
    choice_count: int,
    use_explicit_kinds: bool,
) -> list[OMRQuestionContract]:
    by_number = {int(getattr(question, "number", 0) or 0): question for question in questions}
    contracts: list[OMRQuestionContract] = []
    for number in range(1, total_questions + 1):
        question = by_number.get(number)
        contracts.append(
            OMRQuestionContract(
                number=number,
                exam_question_id=getattr(question, "id", None) if question is not None else None,
                kind=(
                    question.question_kind
                    if use_explicit_kinds and question is not None
                    else ("choice" if number <= choice_count else "essay")
                ),
                score=_score_to_float(getattr(question, "score", None)),
            )
        )
    return contracts


def _score_to_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
