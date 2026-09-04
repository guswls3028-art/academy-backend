"""Tenant-scoped source reads and enrollment locks for exam target edits."""

from django.db.models import F


def target_pairs_for_exam(exam) -> set[tuple[int, int]]:
    from apps.domains.enrollment.models import Enrollment
    from apps.support.progress.session_calculator_dependencies import (
        get_exam_target_enrollment_pairs_for_session,
    )

    pairs = set()
    for session in exam.sessions.filter(lecture__tenant_id=exam.tenant_id):
        enrollment_ids = Enrollment.objects.filter(
            tenant_id=exam.tenant_id,
            student__tenant_id=exam.tenant_id,
            lecture_id=session.lecture_id,
            status="ACTIVE",
            student__deleted_at__isnull=True,
        ).values_list("id", flat=True)
        pairs.update(
            (enrollment_id, session.id)
            for enrollment_id, exam_id in get_exam_target_enrollment_pairs_for_session(
                session=session, enrollment_ids=set(enrollment_ids),
            )
            if exam_id == exam.id
        )
    return pairs


def lock_target_projection_enrollments(*, tenant_id: int, enrollment_ids: set[int]) -> set[int]:
    from apps.domains.enrollment.models import Enrollment

    # A stable order serializes overlapping source writers and the normal progress
    # pipeline. NO KEY UPDATE also permits the writers' deferred FK key-share locks.
    return set(
        Enrollment.objects.filter(
            id__in=enrollment_ids,
            tenant_id=tenant_id,
            lecture__tenant_id=F("tenant_id"),
            student__tenant_id=F("tenant_id"),
            status="ACTIVE",
            student__deleted_at__isnull=True,
        ).select_for_update(of=("self",), no_key=True).order_by("id")
        .values_list("id", flat=True)
    )
