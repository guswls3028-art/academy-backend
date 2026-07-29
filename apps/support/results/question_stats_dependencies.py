"""Cross-domain reads for user-facing question statistics."""

from __future__ import annotations


def question_numbers_by_id_for_exam(
    *,
    exam_id: int,
    question_ids: list[int],
) -> dict[int, int]:
    if not question_ids:
        return {}

    from apps.domains.exams.models import Exam, ExamQuestion

    exam = (
        Exam.objects
        .only("id", "tenant_id", "exam_type", "template_exam_id")
        .filter(id=int(exam_id))
        .first()
    )
    if exam is None:
        return {}

    structure_exam_ids = {
        int(exam.id),
        int(exam.effective_structure_exam_id),
    }
    if exam.template_exam_id:
        structure_exam_ids.add(int(exam.template_exam_id))

    return {
        int(question_id): int(number)
        for question_id, number in (
            ExamQuestion.objects
            .filter(
                id__in=question_ids,
                sheet__exam_id__in=structure_exam_ids,
                sheet__exam__tenant_id=int(exam.tenant_id),
            )
            .values_list("id", "number")
        )
    }
