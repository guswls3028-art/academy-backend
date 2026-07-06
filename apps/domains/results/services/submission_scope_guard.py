from __future__ import annotations

from django.core.exceptions import ValidationError

from apps.support.results.grading_dependencies import (
    get_active_submission_enrollment,
    submission_enrollment_assigned_to_exam,
)


def validate_exam_submission_scope(*, submission, exam):
    """Validate the async grading boundary before any score/result writes."""
    if str(submission.target_type) != "exam" or int(submission.target_id) != int(exam.id):
        raise ValidationError("submission target does not match exam")

    enrollment_id = getattr(submission, "enrollment_id", None)
    if not enrollment_id:
        raise ValidationError("submission enrollment is required")

    enrollment = get_active_submission_enrollment(submission=submission)
    if not enrollment:
        raise ValidationError("submission enrollment is not active in tenant")

    if int(exam.tenant_id) != int(submission.tenant_id) or int(enrollment.tenant_id) != int(submission.tenant_id):
        raise ValidationError("submission, exam, and enrollment tenant mismatch")

    in_exam = submission_enrollment_assigned_to_exam(
        exam_id=int(exam.id),
        enrollment_id=int(enrollment.id),
        tenant_id=int(submission.tenant_id),
    )
    if in_exam:
        return enrollment

    raise ValidationError("submission enrollment is not assigned to exam")
