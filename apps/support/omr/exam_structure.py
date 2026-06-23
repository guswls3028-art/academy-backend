from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from academy.domain.omr import OMRSheetContract

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OmrExamStructure:
    qnum_to_pk: dict[int, int]
    correct_answers_by_pk: dict[str, Any]
    qnum_map_built: bool
    contract: OMRSheetContract | None = None
    contract_snapshot: dict[str, Any] = field(default_factory=dict)

    @property
    def expected_objective_count(self) -> int:
        return int(self.contract.choice_count if self.contract else 0)


def empty_exam_structure() -> OmrExamStructure:
    return OmrExamStructure(qnum_to_pk={}, correct_answers_by_pk={}, qnum_map_built=False)


def load_submission_exam_structure(submission) -> OmrExamStructure:
    if getattr(submission, "target_type", "") != "exam" or not getattr(submission, "target_id", None):
        return empty_exam_structure()

    try:
        from apps.domains.exams.models import AnswerKey, Exam, Sheet
        from apps.domains.exams.services.template_resolver import resolve_template_exam
        from apps.support.omr.contract_builder import build_omr_sheet_contract

        exam = Exam.objects.filter(
            id=int(submission.target_id),
            tenant=getattr(submission, "tenant", None),
        ).order_by("id").first()
        if not exam:
            return empty_exam_structure()

        template_exam = resolve_template_exam(exam)
        qnum_to_pk: dict[int, int] = {}
        qnum_map_built = False
        contract = None
        contract_snapshot: dict[str, Any] = {}
        sheet = Sheet.objects.filter(exam=template_exam).order_by("id").first()
        if sheet:
            contract = build_omr_sheet_contract(sheet=sheet, exam=template_exam)
            qnum_to_pk = contract.objective_question_ids_by_number
            contract_snapshot = contract.to_dict(include_template_meta=False)
            qnum_map_built = True

        correct_answers_by_pk: dict[str, Any] = {}
        answer_key = AnswerKey.objects.filter(exam=template_exam).order_by("id").first()
        if answer_key and isinstance(answer_key.answers, dict):
            correct_answers_by_pk = answer_key.answers

        return OmrExamStructure(
            qnum_to_pk=qnum_to_pk,
            correct_answers_by_pk=correct_answers_by_pk,
            qnum_map_built=qnum_map_built,
            contract=contract,
            contract_snapshot=contract_snapshot,
        )
    except Exception:
        logger.exception(
            "load_submission_exam_structure: failed | submission=%s | exam=%s",
            getattr(submission, "id", None),
            getattr(submission, "target_id", None),
        )
        return empty_exam_structure()
