from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from apps.domains.exams.models import ExamQuestion
from apps.domains.exams.models.sheet import Sheet
from apps.domains.exams.services.template_resolver import resolve_template_exam
from apps.support.omr.contract_builder import build_omr_sheet_contract


QuestionKind = Literal["choice", "essay"]


@dataclass(frozen=True)
class ExamScoreShape:
    exam_id: int
    template_exam_id: int | None
    sheet_id: int | None
    total_questions: int
    choice_count: int
    essay_count: int
    objective_max_score: float
    subjective_max_score: float
    total_max_score: float
    shape_source: str
    question_kind_by_id: dict[int, QuestionKind] = field(default_factory=dict)
    question_number_by_id: dict[int, int] = field(default_factory=dict)

    def question_kind(self, question_id: int) -> QuestionKind | None:
        return self.question_kind_by_id.get(int(question_id))


def get_exam_score_shape(exam) -> ExamScoreShape:
    exam_id = int(getattr(exam, "id", 0) or 0)
    exam_max_score = float(getattr(exam, "max_score", 0.0) or 0.0)

    try:
        template_exam = resolve_template_exam(exam)
    except Exception:
        template_exam = exam

    template_exam_id = (
        int(getattr(template_exam, "id", 0) or 0)
        if template_exam is not None
        else None
    )
    sheet = (
        Sheet.objects.filter(exam_id=template_exam_id).first()
        if template_exam_id is not None
        else None
    )

    if sheet is None:
        return ExamScoreShape(
            exam_id=exam_id,
            template_exam_id=template_exam_id,
            sheet_id=None,
            total_questions=0,
            choice_count=0,
            essay_count=0,
            objective_max_score=exam_max_score,
            subjective_max_score=exam_max_score,
            total_max_score=exam_max_score,
            shape_source="no_sheet",
        )

    contract = build_omr_sheet_contract(sheet=sheet, exam=exam)
    questions = list(
        ExamQuestion.objects.filter(sheet=sheet)
        .only("id", "number", "score")
        .order_by("number")
    )
    question_by_number = {int(q.number): q for q in questions}

    objective_max = 0.0
    subjective_max = 0.0
    kind_by_id: dict[int, QuestionKind] = {}
    number_by_id: dict[int, int] = {}

    for contract_question in contract.questions:
        question = question_by_number.get(int(contract_question.number))
        score = contract_question.score
        if score is None and question is not None:
            score = float(getattr(question, "score", 0.0) or 0.0)
        score_float = float(score or 0.0)

        if question is not None:
            qid = int(question.id)
            kind_by_id[qid] = contract_question.kind  # type: ignore[assignment]
            number_by_id[qid] = int(question.number)

        if contract_question.kind == "choice":
            objective_max += score_float
        else:
            subjective_max += score_float

    component_total = objective_max + subjective_max
    total_max = component_total if component_total > 0 else exam_max_score

    if component_total <= 0 and exam_max_score > 0 and contract.total_questions > 0:
        equal_question_score = exam_max_score / int(contract.total_questions)
        objective_max = equal_question_score * int(contract.choice_count)
        subjective_max = equal_question_score * int(contract.essay_count)
        total_max = exam_max_score
    elif contract.essay_count == 0 and exam_max_score > objective_max:
        objective_max = exam_max_score
        total_max = exam_max_score
    elif exam_max_score > 0 and abs(exam_max_score - component_total) < 0.0001:
        total_max = exam_max_score

    return ExamScoreShape(
        exam_id=exam_id,
        template_exam_id=template_exam_id,
        sheet_id=int(sheet.id),
        total_questions=int(contract.total_questions),
        choice_count=int(contract.choice_count),
        essay_count=int(contract.essay_count),
        objective_max_score=float(objective_max),
        subjective_max_score=float(subjective_max),
        total_max_score=float(total_max),
        shape_source=str(contract.shape_source or "unknown"),
        question_kind_by_id=kind_by_id,
        question_number_by_id=number_by_id,
    )
