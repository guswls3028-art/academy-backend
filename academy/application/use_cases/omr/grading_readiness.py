from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from apps.domains.submissions.models import Submission
from apps.domains.submissions.omr_pipeline.services.facts import (
    OMRGradeReadiness,
    evaluate_omr_grade_readiness,
    write_readiness_meta,
)
from apps.domains.submissions.services.transition import transit


@dataclass(frozen=True)
class OMRGradeDecision:
    submission_id: int
    graded: bool
    readiness: OMRGradeReadiness
    result_id: int | None = None
    status: str = ""
    reason: str = ""


def grade_omr_submission_if_ready(
    submission_id: int,
    *,
    actor: str = "omr.readiness",
    allow_done_regrade: bool = False,
) -> OMRGradeDecision:
    """
    Run OMR grading only after durable student-match and answer facts exist.

    `Submission.status` is a compatibility projection. This use case is the
    canonical gate that prevents manual student matching from producing a
    zero-answer score while still allowing later AI answers to complete grading.
    """
    with transaction.atomic():
        submission = (
            Submission.objects.select_for_update()
            .get(id=int(submission_id))
        )
        readiness = evaluate_omr_grade_readiness(submission)
        write_readiness_meta(
            submission=submission,
            readiness=readiness,
            actor=actor,
        )
        if not readiness.can_grade:
            return OMRGradeDecision(
                submission_id=int(submission.id),
                graded=False,
                readiness=readiness,
                status=str(submission.status),
                reason="not_ready",
            )

        if submission.status == Submission.Status.DONE and not allow_done_regrade:
            return OMRGradeDecision(
                submission_id=int(submission.id),
                graded=False,
                readiness=readiness,
                status=str(submission.status),
                reason="already_done",
            )

        update_fields = ["updated_at"]
        if readiness.enrollment_id and submission.enrollment_id != readiness.enrollment_id:
            submission.enrollment_id = readiness.enrollment_id
            update_fields.append("enrollment_id")

        if submission.status != Submission.Status.ANSWERS_READY:
            transit(
                submission,
                Submission.Status.ANSWERS_READY,
                admin_override=True,
                actor=actor,
            )
            update_fields.extend(["status", "error_message"])

        if len(update_fields) > 1:
            submission.save(update_fields=sorted(set(update_fields)))

    from apps.domains.results.services.grading_service import grade_submission

    result = grade_submission(int(submission_id))
    status = (
        Submission.objects
        .filter(id=int(submission_id))
        .values_list("status", flat=True)
        .first()
    )
    return OMRGradeDecision(
        submission_id=int(submission_id),
        graded=True,
        readiness=readiness,
        result_id=int(getattr(result, "id", 0) or 0) or None,
        status=str(status or ""),
        reason="graded",
    )


def readiness_payload(readiness: OMRGradeReadiness) -> dict:
    return {
        "can_grade": readiness.can_grade,
        "missing": list(readiness.missing),
        "answer_count": readiness.answer_count,
        "enrollment_id": readiness.enrollment_id,
        "manual_review_required": readiness.manual_review_required,
        "status": readiness.status,
    }
