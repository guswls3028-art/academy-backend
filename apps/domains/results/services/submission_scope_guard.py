from __future__ import annotations

from django.core.exceptions import ValidationError


def validate_exam_submission_scope(*, submission, exam):
    """Validate the async grading boundary before any score/result writes."""
    if str(submission.target_type) != "exam" or int(submission.target_id) != int(exam.id):
        raise ValidationError("submission target does not match exam")

    enrollment_id = getattr(submission, "enrollment_id", None)
    if not enrollment_id:
        raise ValidationError("submission enrollment is required")

    from apps.domains.enrollment.models import Enrollment
    from apps.domains.exams.models import ExamEnrollment

    enrollment = (
        Enrollment.objects
        .filter(
            id=int(enrollment_id),
            tenant_id=int(submission.tenant_id),
            status="ACTIVE",
            student__deleted_at__isnull=True,
        )
        .select_related("student", "lecture")
        .first()
    )
    if not enrollment:
        raise ValidationError("submission enrollment is not active in tenant")

    if int(exam.tenant_id) != int(submission.tenant_id) or int(enrollment.tenant_id) != int(submission.tenant_id):
        raise ValidationError("submission, exam, and enrollment tenant mismatch")

    in_exam = ExamEnrollment.objects.filter(
        exam_id=int(exam.id),
        enrollment_id=int(enrollment.id),
        enrollment__tenant_id=int(submission.tenant_id),
    ).exists()
    if in_exam:
        return enrollment

    raise ValidationError("submission enrollment is not assigned to exam")
