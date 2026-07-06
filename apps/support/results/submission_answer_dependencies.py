"""Cross-domain submission answer dependencies for result sync."""

from __future__ import annotations

from typing import Any


def build_answer_text_by_question_id_from_submission(
    *,
    submission: Any,
    question_number_to_id: dict[int, int] | None,
) -> dict[int, str]:
    from apps.domains.submissions.selectors import build_answer_text_by_question_id_from_submission as _build

    return _build(
        submission=submission,
        question_number_to_id=question_number_to_id,
    )


def is_omr_scan_submission(submission: Any) -> bool:
    from apps.domains.submissions.selectors import is_omr_scan_submission as _is_omr

    return _is_omr(submission)
