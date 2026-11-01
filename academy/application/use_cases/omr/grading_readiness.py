from __future__ import annotations

from dataclasses import dataclass, replace

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.domains.submissions.models import Submission
from apps.domains.submissions.omr_pipeline.services.facts import (
    OMRGradeReadiness,
    evaluate_omr_grade_readiness,
    write_readiness_meta,
)
from apps.domains.submissions.services.lifecycle import reopen_for_regrade_in_memory
from apps.support.submissions.grading_contract_dependencies import has_submission_grading_result


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
    preserve_current_omr_grade: bool = False,
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
        configuration_missing = {"answer_key", "sheet", "grading_configuration"}
        missing_configuration = set(readiness.missing) & configuration_missing
        if missing_configuration and (
            submission.status == Submission.Status.DONE
            or has_submission_grading_result(
                tenant_id=int(submission.tenant_id), exam_id=int(submission.target_id),
                submission_id=int(submission.id),
            )
        ):
            # Editing existing scored answers must stay atomic with regrading.
            # A pending setup response must not leave old published scores.
            raise ValidationError(
                "Existing OMR results require valid grading configuration.",
                code="omr_grading_configuration_conflict",
                params={"missing": sorted(missing_configuration)},
            )
        if (
            readiness.missing
            and set(readiness.missing) <= configuration_missing
            and submission.status not in (Submission.Status.DONE, Submission.Status.ANSWERS_READY)
        ):
            # Identity and recognized answers are durable even before the key
            # is configured. The normal key-save regrade consumes this state.
            reopen_for_regrade_in_memory(submission, actor=actor)
            submission.save(update_fields=["status", "error_message", "updated_at"])
            readiness = replace(readiness, status=str(submission.status))
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
            reopen_for_regrade_in_memory(submission, actor=actor)
            update_fields.extend(["status", "error_message"])

        if len(update_fields) > 1:
            submission.save(update_fields=sorted(set(update_fields)))

    from apps.domains.results.services.grading_service import grade_submission

    result = grade_submission(
        int(submission_id),
        force_regrade=allow_done_regrade,
        **({"preserve_current_omr_grade": True} if preserve_current_omr_grade else {}),
    )
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
