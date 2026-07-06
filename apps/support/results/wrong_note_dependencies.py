"""Cross-domain read helpers for wrong-note result services."""

from __future__ import annotations

from typing import Any


def regular_exam_ids_by_lecture_and_order(*, lecture_id: int, from_order: int) -> list[int]:
    from apps.domains.exams.models import Exam

    return list(
        Exam.objects.filter(
            exam_type=Exam.ExamType.REGULAR,
            sessions__lecture_id=int(lecture_id),
            sessions__order__gte=int(from_order),
        )
        .values_list("id", flat=True)
        .distinct()
    )


def answer_key_map_for_effective_exam(*, exam_id: int) -> dict[str, Any]:
    from apps.domains.exams.models import AnswerKey, Exam

    exam = Exam.objects.only("id", "exam_type", "template_exam_id").filter(id=int(exam_id)).first()
    if exam is None:
        return {}
    answer_key = AnswerKey.objects.filter(exam_id=exam.effective_template_exam_id).first()
    answers = getattr(answer_key, "answers", None) if answer_key else None
    return answers if isinstance(answers, dict) else {}


def exam_questions_by_id(*, question_ids: list[int]) -> dict[int, Any]:
    from apps.domains.exams.models import ExamQuestion

    return ExamQuestion.objects.filter(id__in=question_ids).select_related("sheet").in_bulk(field_name="id")

