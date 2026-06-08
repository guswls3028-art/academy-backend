from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from django.db.models import F, Q

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

    def question_max_score(
        self,
        question_id: int,
        raw_max_score: float | int | None = None,
    ) -> float:
        raw = float(raw_max_score or 0.0)
        if raw > 0:
            return raw
        kind = self.question_kind(int(question_id))
        if kind == "choice" and self.choice_count > 0 and self.objective_max_score > 0:
            return float(self.objective_max_score) / int(self.choice_count)
        if kind == "essay" and self.essay_count > 0 and self.subjective_max_score > 0:
            return float(self.subjective_max_score) / int(self.essay_count)
        return 0.0

    def question_potential_max_score(
        self,
        question_id: int,
        raw_max_score: float | int | None = None,
    ) -> float:
        active_max_score = self.question_max_score(question_id, raw_max_score)
        if active_max_score > 0:
            return active_max_score

        kind = self.question_kind(int(question_id))
        if kind not in {"choice", "essay"}:
            return 0.0
        if self.total_questions > 0 and self.total_max_score > 0:
            return float(self.total_max_score) / int(self.total_questions)
        return 0.0


def _answer_value_present(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        normalized = "".join(value.strip().lower().split())
        return bool(normalized) and normalized not in {
            "-",
            "n/a",
            "na",
            "none",
            "없음",
            "해설없음",
            "정답없음",
        }
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _has_essay_scoring_evidence(
    *,
    exam_id: int,
    template_exam_id: int | None,
    essay_question_ids: set[int],
) -> bool:
    if not essay_question_ids:
        return False

    from apps.domains.exams.models import AnswerKey
    from apps.domains.results.models import Result, ResultFact, ResultItem

    answer_key = (
        AnswerKey.objects.filter(exam_id=int(template_exam_id)).first()
        if template_exam_id is not None
        else None
    )
    if answer_key and isinstance(answer_key.answers, dict):
        for question_id in essay_question_ids:
            if _answer_value_present(answer_key.answers.get(str(question_id))):
                return True

    if ResultItem.objects.filter(
        result__target_type="exam",
        result__target_id=int(exam_id),
        question_id__in=essay_question_ids,
    ).filter(Q(score__gt=0) | Q(max_score__gt=0)).exists():
        return True

    if ResultFact.objects.filter(
        target_type="exam",
        target_id=int(exam_id),
    ).filter(
        Q(question_id__in=essay_question_ids)
        | Q(source__icontains="subjective")
    ).filter(Q(score__gt=0) | Q(max_score__gt=0)).exists():
        return True

    return Result.objects.filter(
        target_type="exam",
        target_id=int(exam_id),
        total_score__gt=F("objective_score"),
    ).exists()


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
    shape_source = str(contract.shape_source or "unknown")

    if component_total <= 0 and exam_max_score > 0 and contract.total_questions > 0:
        essay_question_ids = {
            int(question_id)
            for question_id, kind in kind_by_id.items()
            if kind == "essay"
        }
        has_scoreable_essay = _has_essay_scoring_evidence(
            exam_id=exam_id,
            template_exam_id=template_exam_id,
            essay_question_ids=essay_question_ids,
        )
        if contract.choice_count > 0 and contract.essay_count > 0 and not has_scoreable_essay:
            objective_max = exam_max_score
            subjective_max = 0.0
            shape_source = f"{shape_source}:decorative_essay"
        else:
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
        shape_source=shape_source,
        question_kind_by_id=kind_by_id,
        question_number_by_id=number_by_id,
    )
