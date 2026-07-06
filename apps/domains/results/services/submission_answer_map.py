from __future__ import annotations

from django.core.exceptions import ValidationError
from apps.support.results.submission_answer_dependencies import (
    build_answer_text_by_question_id_from_submission,
    is_omr_scan_submission,
)


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
    return build_answer_text_by_question_id_from_submission(
        submission=submission,
        question_number_to_id=question_number_to_id,
    )


def require_complete_omr_answers(
    *,
    submission,
    answers_map: dict[int, str],
    expected_question_ids: set[int],
    context: str,
    protect_existing_score: bool,
) -> None:
    if not is_omr_scan_submission(submission):
        return
    if not expected_question_ids:
        return

    missing = sorted(int(qid) for qid in expected_question_ids if int(qid) not in answers_map)
    if not missing:
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
