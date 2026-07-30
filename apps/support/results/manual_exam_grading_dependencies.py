"""Cross-domain reads and locks used by the manual exam grading workflow."""

from __future__ import annotations

from typing import Any


def get_locked_exam_questions_for_manual_grading(
    *,
    question_ids: set[int],
    tenant: Any,
) -> dict[int, Any]:
    from apps.domains.exams.models import ExamQuestion

    return {
        int(question.id): question
        for question in ExamQuestion.objects.select_for_update()
        .filter(
            id__in=question_ids,
            sheet__exam__tenant=tenant,
        )
        .select_related("sheet")
    }
