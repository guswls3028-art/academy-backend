"""Bounded reads for the existing score-shape rules; no cross-request cache."""
from collections import defaultdict
from dataclasses import dataclass

from django.db.models import F, Q

from apps.domains.exams.models import AnswerKey, Exam, ExamQuestion, Sheet
from apps.domains.results.models import Result, ResultFact, ResultItem
from apps.support.omr.contract_builder import build_omr_sheet_contract_from_data
from apps.support.omr.score_shape import (
    ExamScoreShape,
    _answer_value_present,
    build_exam_score_shape_from_data,
)


@dataclass(frozen=True)
class ExamScoringDetails:
    shape: ExamScoreShape
    answers: dict


def load_exam_scoring_details(exams) -> dict[int, ExamScoringDetails]:
    exams = {int(exam.id): exam for exam in exams}
    if not exams:
        return {}
    template_ids = {exam.template_exam_id for exam in exams.values() if exam.template_exam_id}
    templates = Exam.objects.filter(
        id__in=template_ids, exam_type=Exam.ExamType.TEMPLATE,
    ).in_bulk()
    sheets = {sheet.exam_id: sheet for sheet in Sheet.objects.filter(
        exam_id__in=set(exams) | set(templates),
    ).select_related("exam")}
    # Same precedence as resolve_structure_exam: an owned snapshot wins, then
    # a legacy template; an unresolved template keeps the no-sheet fallback.
    owners = {
        exam_id: (
            exam if exam.exam_type == Exam.ExamType.TEMPLATE or exam_id in sheets
            else templates.get(exam.template_exam_id, exam)
        )
        for exam_id, exam in exams.items()
    }
    owner_ids = {owner.id for owner in owners.values()}
    questions = defaultdict(list)
    for question in ExamQuestion.objects.filter(
        sheet_id__in=[sheet.id for owner_id, sheet in sheets.items() if owner_id in owner_ids],
    ).order_by("number"):
        questions[question.sheet_id].append(question)
    answers = dict(AnswerKey.objects.filter(exam_id__in=owner_ids).values_list("exam_id", "answers"))
    inputs = {}
    shapes = {}
    evidence_needed = {}

    def collect_evidence(*, exam_id, template_exam_id, essay_question_ids):
        if not essay_question_ids:
            return False
        answer_map = answers.get(template_exam_id)
        if isinstance(answer_map, dict) and any(
            _answer_value_present(answer_map.get(str(qid))) for qid in essay_question_ids
        ):
            return True
        evidence_needed[exam_id] = essay_question_ids
        return False

    for exam_id, exam in exams.items():
        owner = owners[exam_id]
        sheet = sheets.get(owner.id)
        sheet_questions = questions.get(sheet.id, []) if sheet else []
        answer_map = answers.get(owner.id)
        contract = build_omr_sheet_contract_from_data(
            sheet=sheet, template_exam=owner, questions=sheet_questions, answers=answer_map,
        ) if sheet else None
        inputs[exam_id] = dict(
            exam=exam, template_exam_id=owner.id, sheet=sheet, contract=contract,
            questions=sheet_questions, answers=answer_map,
        )
        shapes[exam_id] = build_exam_score_shape_from_data(
            **inputs[exam_id], essay_scoring_evidence=collect_evidence,
        )
    # Only legacy zero-weight sheets need grading evidence. Query their facts
    # together, preserving the per-exam/question pair even for shared templates.
    for exam_id in _exams_with_essay_evidence(evidence_needed):
        shapes[exam_id] = build_exam_score_shape_from_data(
            **inputs[exam_id], essay_scoring_evidence=lambda **_: True,
        )
    return {
        exam_id: ExamScoringDetails(shape=shape, answers=answers.get(owners[exam_id].id) or {})
        for exam_id, shape in shapes.items()
    }


def _exams_with_essay_evidence(question_ids_by_exam):
    if not question_ids_by_exam:
        return set()
    question_ids = set().union(*question_ids_by_exam.values())
    found = {
        exam_id
        for exam_id, question_id in ResultItem.objects.filter(
            result__target_type="exam", result__target_id__in=question_ids_by_exam,
            question_id__in=question_ids,
        ).filter(Q(score__gt=0) | Q(max_score__gt=0))
        .order_by().values_list("result__target_id", "question_id").distinct()
        if question_id in question_ids_by_exam[exam_id]
    }
    facts = ResultFact.objects.filter(
        target_type="exam", target_id__in=question_ids_by_exam,
    ).filter(Q(score__gt=0) | Q(max_score__gt=0)).order_by()
    found.update(facts.filter(source__icontains="subjective").values_list("target_id", flat=True).distinct())
    found.update(
        exam_id for exam_id, question_id in facts.filter(question_id__in=question_ids)
        .values_list("target_id", "question_id").distinct()
        if question_id in question_ids_by_exam[exam_id]
    )
    found.update(Result.objects.filter(
        target_type="exam", target_id__in=question_ids_by_exam,
        total_score__gt=F("objective_score"),
    ).order_by().values_list("target_id", flat=True).distinct())
    return found
