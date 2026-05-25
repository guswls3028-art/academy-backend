from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OmrExamStructure:
    qnum_to_pk: dict[int, int]
    correct_answers_by_pk: dict[str, Any]
    qnum_map_built: bool


def empty_exam_structure() -> OmrExamStructure:
    return OmrExamStructure(qnum_to_pk={}, correct_answers_by_pk={}, qnum_map_built=False)


def load_submission_exam_structure(submission) -> OmrExamStructure:
    if getattr(submission, "target_type", "") != "exam" or not getattr(submission, "target_id", None):
        return empty_exam_structure()

    try:
        from apps.domains.exams.models import AnswerKey, Exam, ExamQuestion, Sheet
        from apps.domains.exams.services.template_resolver import resolve_template_exam

        exam = Exam.objects.filter(
            id=int(submission.target_id),
            tenant=getattr(submission, "tenant", None),
        ).first()
        if not exam:
            return empty_exam_structure()

        template_exam = resolve_template_exam(exam)
        qnum_to_pk: dict[int, int] = {}
        qnum_map_built = False
        sheet = Sheet.objects.filter(exam=template_exam).first()
        if sheet:
            for q in ExamQuestion.objects.filter(sheet=sheet).only("id", "number"):
                qnum_to_pk[int(q.number)] = int(q.id)
            qnum_map_built = True

        correct_answers_by_pk: dict[str, Any] = {}
        answer_key = AnswerKey.objects.filter(exam=template_exam).first()
        if answer_key and isinstance(answer_key.answers, dict):
            correct_answers_by_pk = answer_key.answers

        return OmrExamStructure(
            qnum_to_pk=qnum_to_pk,
            correct_answers_by_pk=correct_answers_by_pk,
            qnum_map_built=qnum_map_built,
        )
    except Exception:
        logger.exception(
            "load_submission_exam_structure: failed | submission=%s | exam=%s",
            getattr(submission, "id", None),
            getattr(submission, "target_id", None),
        )
        return empty_exam_structure()
