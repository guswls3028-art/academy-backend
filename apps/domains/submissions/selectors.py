from __future__ import annotations

from typing import Any

from apps.domains.submissions.models import OMRDetectedAnswer, OMRRecognitionRun, Submission, SubmissionAnswer


def is_omr_scan_submission(submission: Any) -> bool:
    return str(getattr(submission, "source", "") or "") == Submission.Source.OMR_SCAN


def build_answer_text_by_question_id_from_submission(
    *,
    submission: Any,
    question_number_to_id: dict[int, int] | None = None,
) -> dict[int, str]:
    """Return objective answer text keyed by ExamQuestion id for a submission."""

    answers_map: dict[int, str] = {}
    for answer in SubmissionAnswer.objects.filter(submission=submission):
        qid = int(getattr(answer, "exam_question_id", 0) or 0)
        if qid > 0:
            answers_map[qid] = str(getattr(answer, "answer", "") or "").strip()

    if not is_omr_scan_submission(submission):
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
