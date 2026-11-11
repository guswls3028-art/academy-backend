"""Cross-domain reads and locks used by the manual exam grading workflow."""

from __future__ import annotations

from typing import Any


def get_locked_exam_questions_for_manual_grading(
    *,
    question_ids: set[int],
    tenant: Any,
) -> dict[int, Any]:
    from apps.domains.exams.models import ExamQuestion

    return {
        int(question.id): question
        for question in ExamQuestion.objects.select_for_update()
        .filter(
            id__in=question_ids,
            sheet__exam__tenant=tenant,
        )
        .select_related("sheet")
    }


def omr_objective_sources_for_manual_grading(
    *,
    tenant: Any,
    exam_id: int,
    submission_ids: set[int],
) -> dict[int, dict[str, bool]]:
    """Describe the paper scans attached to grading-grid results.

    Only scans of this tenant and exam are returned. ``gradable`` means the
    scan finished automatic grading and can be re-read without the worker.
    """
    from apps.domains.submissions.models import Submission

    if not submission_ids:
        return {}
    sources: dict[int, dict[str, bool]] = {}
    for submission_id, status, meta in Submission.objects.filter(
        id__in={int(submission_id) for submission_id in submission_ids},
        tenant=tenant,
        target_type=Submission.TargetType.EXAM,
        target_id=int(exam_id),
        source=Submission.Source.OMR_SCAN,
    ).values_list("id", "status", "meta"):
        manual_review = (
            meta.get("manual_review")
            if isinstance(meta, dict) and isinstance(meta.get("manual_review"), dict)
            else {}
        )
        sources[int(submission_id)] = {
            "manual_review_required": manual_review.get("required") is True,
            "gradable": status == Submission.Status.DONE,
        }
    return sources


def lock_omr_submission_for_manual_grading(
    *,
    tenant: Any,
    exam_id: int,
    enrollment_id: int,
    submission_id: int,
) -> bool:
    """Lock the attached scan before the enrollment/result rows it grades."""
    from apps.domains.submissions.models import Submission

    submission = (
        Submission.objects.select_for_update()
        .filter(
            id=int(submission_id),
            tenant=tenant,
            target_type=Submission.TargetType.EXAM,
            target_id=int(exam_id),
            enrollment_id=int(enrollment_id),
            source=Submission.Source.OMR_SCAN,
        )
        .first()
    )
    if submission is None or submission.status != Submission.Status.DONE:
        return False
    meta = submission.meta if isinstance(submission.meta, dict) else {}
    manual_review = meta.get("manual_review") if isinstance(meta.get("manual_review"), dict) else {}
    return manual_review.get("required") is not True
