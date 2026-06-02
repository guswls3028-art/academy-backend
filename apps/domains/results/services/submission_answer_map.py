from __future__ import annotations

from django.core.exceptions import ValidationError


def build_submission_answers_map(
    *,
    submission,
    question_number_to_id: dict[int, int] | None = None,
) -> dict[int, str]:
    """
    Build the grading answer map from compatibility rows, with OMR fact fallback.

    New OMR scans should have SubmissionAnswer rows. Some production-era scans
    only have durable OMRDetectedAnswer facts, so re-sync must be able to read
    those facts without treating every objective answer as blank.
    """
    from apps.domains.submissions.models import (
        OMRDetectedAnswer,
        OMRRecognitionRun,
        Submission,
        SubmissionAnswer,
    )

    answers_map: dict[int, str] = {}
    for answer in SubmissionAnswer.objects.filter(submission=submission):
        qid = int(getattr(answer, "exam_question_id", 0) or 0)
        if qid > 0:
            answers_map[qid] = str(getattr(answer, "answer", "") or "").strip()

    if submission.source != Submission.Source.OMR_SCAN:
        return answers_map

    latest_run = (
        OMRRecognitionRun.objects
        .filter(submission=submission, answer_count__gt=0)
        .order_by("-received_at", "-id")
        .first()
    )
    if latest_run is None:
        return answers_map

    qnum_to_id = {
        int(k): int(v)
        for k, v in (question_number_to_id or {}).items()
        if int(k) > 0 and int(v) > 0
    }
    for detected in OMRDetectedAnswer.objects.filter(recognition_run=latest_run):
        qid = int(getattr(detected, "exam_question_id", 0) or 0)
        if qid <= 0:
            qid = qnum_to_id.get(int(detected.question_number or 0), 0)
        if qid <= 0 or qid in answers_map:
            continue

        answer_text = str(getattr(detected, "answer", "") or "").strip()
        if not answer_text:
            raw_detected = getattr(detected, "detected", None)
            if isinstance(raw_detected, list):
                answer_text = ",".join(str(v).strip() for v in raw_detected if str(v).strip())
        answers_map[qid] = answer_text

    return answers_map


def require_complete_omr_answers(
    *,
    submission,
    answers_map: dict[int, str],
    expected_question_ids: set[int],
    context: str,
    protect_existing_score: bool,
) -> None:
    from apps.domains.submissions.models import Submission

    if submission.source != Submission.Source.OMR_SCAN:
        return
    if not expected_question_ids:
        return

    missing = sorted(int(qid) for qid in expected_question_ids if int(qid) not in answers_map)
    if not missing:
        return
    if not protect_existing_score:
        return

    raise ValidationError(
        {
            "detail": (
                f"OMR answers incomplete for submission {submission.id}: "
                f"{len(expected_question_ids) - len(missing)}/{len(expected_question_ids)}"
            ),
            "code": "OMR_ANSWERS_INCOMPLETE",
            "context": context,
            "missing_question_ids": missing[:20],
        }
    )
