"""Cross-domain dependencies for clinic trigger services."""

from __future__ import annotations


def get_enrollment_tenant_id(enrollment_id: int) -> int | None:
    from apps.domains.enrollment.public_queries import (
        get_enrollment_tenant_id as _get_enrollment_tenant_id,
    )

    return _get_enrollment_tenant_id(int(enrollment_id))


def is_representative_exam_not_submitted(*, enrollment_id: int, exam_id: int) -> bool:
    from apps.domains.results.models import ExamAttempt

    return ExamAttempt.objects.filter(
        enrollment_id=int(enrollment_id),
        exam_id=int(exam_id),
        is_representative=True,
        meta__status="NOT_SUBMITTED",
    ).exists()


def lock_enrollment_for_clinic_trigger(*, enrollment_id: int) -> None:
    from apps.domains.enrollment.models import Enrollment

    Enrollment.objects.select_for_update().only("id").get(id=int(enrollment_id))


def locked_representative_exam_is_not_submitted(
    *,
    enrollment_id: int,
    exam_id: int,
) -> bool:
    from apps.domains.results.models import ExamAttempt

    meta = (
        ExamAttempt.objects.select_for_update()
        .filter(
            enrollment_id=int(enrollment_id),
            exam_id=int(exam_id),
            is_representative=True,
        )
        .values_list("meta", flat=True)
        .first()
    )
    return isinstance(meta, dict) and meta.get("status") == "NOT_SUBMITTED"
