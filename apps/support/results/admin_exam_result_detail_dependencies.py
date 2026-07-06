"""Cross-domain dependencies for admin exam result detail views."""

from __future__ import annotations

from typing import Any


def get_answer_key_answers(*, template_exam_id: int | None) -> dict:
    from apps.domains.exams.models import AnswerKey

    try:
        answer_key = AnswerKey.objects.get(exam_id=template_exam_id)
    except AnswerKey.DoesNotExist:
        return {}
    return answer_key.answers or {}


def get_exam_questions_for_sheet(*, sheet_id: int | None) -> list[Any]:
    if not sheet_id:
        return []

    from apps.domains.exams.models import ExamQuestion

    return list(
        ExamQuestion.objects
        .filter(sheet_id=sheet_id)
        .only("id", "number", "score")
        .order_by("number")
    )


def get_omr_submission_for_tenant(*, submission_id: int, tenant: Any) -> Any | None:
    from apps.domains.submissions.models import Submission

    return (
        Submission.objects
        .filter(id=submission_id, tenant=tenant)
        .only("id", "file_key", "status", "meta", "source")
        .first()
    )


def is_omr_scan_submission(submission: Any) -> bool:
    from apps.domains.submissions.models import Submission

    return bool(
        getattr(submission, "file_key", "")
        and getattr(submission, "source", None) == Submission.Source.OMR_SCAN
    )


def get_omr_answer_meta_by_question_id(*, submission_id: int) -> dict[int, dict]:
    from apps.domains.submissions.models import SubmissionAnswer

    omr_by_qid: dict[int, dict] = {}
    answers = SubmissionAnswer.objects.filter(submission_id=submission_id).only(
        "exam_question_id",
        "meta",
    )
    for answer in answers:
        meta = answer.meta or {}
        omr = meta.get("omr") if isinstance(meta, dict) else None
        if isinstance(omr, dict) and omr:
            omr_by_qid[int(answer.exam_question_id)] = omr
    return omr_by_qid
