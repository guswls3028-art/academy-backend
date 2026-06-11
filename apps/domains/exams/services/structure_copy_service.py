from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.db import transaction

from apps.domains.exams.models import (
    AnswerKey,
    Exam,
    ExamAsset,
    ExamQuestion,
    QuestionExplanation,
    Sheet,
)


@dataclass
class StructureCopyResult:
    copied: bool
    owner_exam_id: int
    source_exam_id: int | None = None
    question_id_map: dict[int, int] = field(default_factory=dict)
    remapped_counts: dict[str, int] = field(default_factory=dict)


def exam_has_own_sheet(exam: Exam) -> bool:
    return Sheet.objects.filter(exam_id=int(exam.id)).exists()


@transaction.atomic
def copy_exam_structure(
    *,
    source_exam: Exam,
    target_exam: Exam,
    remap_existing_references: bool = False,
) -> StructureCopyResult:
    """
    Copy a template/source exam structure into a regular exam snapshot.

    The target keeps its own Sheet/Question/AnswerKey/Asset rows so later answer
    and score edits cannot mutate the source template or sibling regular exams.
    """
    if exam_has_own_sheet(target_exam):
        return StructureCopyResult(
            copied=False,
            owner_exam_id=int(target_exam.id),
            source_exam_id=int(source_exam.id),
        )

    source_sheet = Sheet.objects.filter(exam=source_exam).first()
    if source_sheet is None:
        Sheet.objects.create(
            exam=target_exam,
            name="MAIN",
            total_questions=0,
            choice_count=0,
            essay_count=0,
        )
        _copy_assets(source_exam=source_exam, target_exam=target_exam)
        return StructureCopyResult(
            copied=True,
            owner_exam_id=int(target_exam.id),
            source_exam_id=int(source_exam.id),
        )

    target_sheet = Sheet.objects.create(
        exam=target_exam,
        name=source_sheet.name,
        total_questions=source_sheet.total_questions,
        choice_count=source_sheet.choice_count,
        essay_count=source_sheet.essay_count,
        file=source_sheet.file,
    )

    question_id_map: dict[int, int] = {}
    for source_question in (
        source_sheet.questions.select_related("explanation").order_by("number", "id")
    ):
        copied_question = ExamQuestion.objects.create(
            sheet=target_sheet,
            number=source_question.number,
            score=source_question.score,
            image=source_question.image,
            image_key=source_question.image_key,
            region_meta=source_question.region_meta,
        )
        question_id_map[int(source_question.id)] = int(copied_question.id)

        explanation = getattr(source_question, "explanation", None)
        if isinstance(explanation, QuestionExplanation):
            QuestionExplanation.objects.create(
                question=copied_question,
                text=explanation.text,
                image_key=explanation.image_key,
                source=explanation.source,
                match_confidence=explanation.match_confidence,
            )

    _copy_or_remap_answer_key(
        source_exam=source_exam,
        target_exam=target_exam,
        question_id_map=question_id_map,
    )
    _copy_assets(source_exam=source_exam, target_exam=target_exam)

    remapped_counts: dict[str, int] = {}
    if remap_existing_references and question_id_map:
        remapped_counts = remap_exam_question_references(
            exam=target_exam,
            question_id_map=question_id_map,
        )

    return StructureCopyResult(
        copied=True,
        owner_exam_id=int(target_exam.id),
        source_exam_id=int(source_exam.id),
        question_id_map=question_id_map,
        remapped_counts=remapped_counts,
    )


@transaction.atomic
def ensure_regular_exam_owns_structure(
    exam: Exam,
    *,
    remap_existing_references: bool = True,
) -> StructureCopyResult:
    if exam.exam_type != Exam.ExamType.REGULAR:
        return StructureCopyResult(copied=False, owner_exam_id=int(exam.id))

    locked_exam = Exam.objects.select_for_update().get(id=int(exam.id))
    if exam_has_own_sheet(locked_exam) or not locked_exam.template_exam_id:
        return StructureCopyResult(copied=False, owner_exam_id=int(locked_exam.id))

    source_exam = Exam.objects.select_for_update().get(
        id=int(locked_exam.template_exam_id),
        tenant_id=int(locked_exam.tenant_id),
        exam_type=Exam.ExamType.TEMPLATE,
    )
    return copy_exam_structure(
        source_exam=source_exam,
        target_exam=locked_exam,
        remap_existing_references=remap_existing_references,
    )


def _copy_or_remap_answer_key(
    *,
    source_exam: Exam,
    target_exam: Exam,
    question_id_map: dict[int, int],
) -> None:
    target_answer_key = AnswerKey.objects.filter(exam=target_exam).first()
    if target_answer_key is not None:
        target_answer_key.answers = remap_answer_keys(
            target_answer_key.answers or {},
            question_id_map,
        )
        target_answer_key.save(update_fields=["answers", "updated_at"])
        return

    source_answer_key = AnswerKey.objects.filter(exam=source_exam).first()
    if source_answer_key is None:
        return

    AnswerKey.objects.create(
        exam=target_exam,
        answers=remap_answer_keys(source_answer_key.answers or {}, question_id_map),
    )


def _copy_assets(*, source_exam: Exam, target_exam: Exam) -> None:
    for asset in source_exam.assets.order_by("asset_type", "id"):
        ExamAsset.objects.update_or_create(
            exam=target_exam,
            asset_type=asset.asset_type,
            defaults={
                "file_key": asset.file_key,
                "file_type": asset.file_type,
                "file_size": asset.file_size,
            },
        )


def remap_answer_keys(
    answers: dict[str, Any],
    question_id_map: dict[int, int],
) -> dict[str, Any]:
    remapped: dict[str, Any] = {}
    for key, value in (answers or {}).items():
        try:
            next_key = str(question_id_map.get(int(key), int(key)))
        except (TypeError, ValueError):
            next_key = str(key)
        remapped[next_key] = value
    return remapped


def remap_exam_question_references(
    *,
    exam: Exam,
    question_id_map: dict[int, int],
) -> dict[str, int]:
    from apps.domains.results.models import ExamResult, Result, ResultFact, ResultItem
    from apps.domains.submissions.models import (
        OMRDetectedAnswer,
        Submission,
        SubmissionAnswer,
    )

    if not question_id_map:
        return {}

    submissions = Submission.objects.filter(
        tenant=exam.tenant,
        target_type=Submission.TargetType.EXAM,
        target_id=int(exam.id),
    )
    results = Result.objects.filter(target_type="exam", target_id=int(exam.id))

    counts = {
        "submission_answers": 0,
        "omr_detected_answers": 0,
        "result_items": 0,
        "result_facts": 0,
        "exam_results": 0,
    }
    for old_id, new_id in question_id_map.items():
        counts["submission_answers"] += SubmissionAnswer.objects.filter(
            submission__in=submissions,
            exam_question_id=int(old_id),
        ).update(exam_question_id=int(new_id))
        counts["omr_detected_answers"] += OMRDetectedAnswer.objects.filter(
            submission__in=submissions,
            exam_question_id=int(old_id),
        ).update(exam_question_id=int(new_id))
        counts["result_items"] += ResultItem.objects.filter(
            result__in=results,
            question_id=int(old_id),
        ).update(question_id=int(new_id))
        counts["result_facts"] += ResultFact.objects.filter(
            target_type="exam",
            target_id=int(exam.id),
            question_id=int(old_id),
        ).update(question_id=int(new_id))

    for exam_result in ExamResult.objects.filter(exam=exam):
        changed = False
        breakdown = exam_result.breakdown or {}
        if isinstance(breakdown, dict):
            for item in breakdown.values():
                if not isinstance(item, dict):
                    continue
                raw_question_id = item.get("question_id")
                try:
                    old_question_id = int(raw_question_id)
                except (TypeError, ValueError):
                    continue
                new_question_id = question_id_map.get(old_question_id)
                if new_question_id is not None:
                    item["question_id"] = int(new_question_id)
                    changed = True
        if changed:
            exam_result.breakdown = breakdown
            exam_result.save(update_fields=["breakdown", "updated_at"])
            counts["exam_results"] += 1

    return counts
