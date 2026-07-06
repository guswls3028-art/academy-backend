"""Cross-domain dependencies for results grading contracts."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError


def validate_exam_grading_contract(exam: Any) -> tuple[Any, Any]:
    from apps.domains.exams.models import Exam
    from apps.domains.exams.models.answer_key import AnswerKey
    from apps.domains.exams.models.sheet import Sheet
    from apps.domains.exams.services.template_resolver import resolve_template_exam

    if exam.exam_type != Exam.ExamType.REGULAR:
        raise ValidationError("only REGULAR exams are gradable")

    template_exam = resolve_template_exam(exam)

    sheet = getattr(template_exam, "sheet", None)
    if not isinstance(sheet, Sheet):
        raise ValidationError("template exam must have a valid sheet")

    answer_key = getattr(template_exam, "answer_key", None)
    if not isinstance(answer_key, AnswerKey):
        raise ValidationError("template exam must have an answer_key")

    if not isinstance(answer_key.answers, dict):
        raise ValidationError("answer_key.answers must be a dict")

    def has_answer_value(value: Any) -> bool:
        if isinstance(value, list):
            return any(str(v).strip() for v in value)
        return bool(str(value or "").strip())

    question_ids = {int(q.id) for q in sheet.questions.all()}
    unknown_key_ids = {
        int(k)
        for k, value in answer_key.answers.items()
        if (
            isinstance(k, (int, str))
            and str(k).isdigit()
            and int(k) not in question_ids
            and has_answer_value(value)
        )
    }

    if unknown_key_ids:
        raise ValidationError("answer_key contains unknown question ids")

    return sheet, answer_key
