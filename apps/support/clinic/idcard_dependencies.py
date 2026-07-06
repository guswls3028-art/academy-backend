"""Cross-domain dependencies for the student clinic ID card API."""

from __future__ import annotations

from typing import Any


def student_for_idcard_user(*, tenant: Any, user: Any) -> Any | None:
    from apps.domains.students.selectors import student_for_tenant_user

    return student_for_tenant_user(tenant, user, deleted="active")


def latest_active_enrollment_for_student(*, tenant: Any, student: Any) -> Any | None:
    from apps.domains.enrollment.selectors import enrollments_for_tenant

    return (
        enrollments_for_tenant(tenant)
        .filter(student=student, status="ACTIVE")
        .select_related("lecture")
        .order_by("-enrolled_at", "-id")
        .first()
    )


def ordered_sessions_for_enrollment(enrollment: Any) -> list[Any]:
    from apps.domains.lectures.models import Session as LectureSession

    session_qs = LectureSession.objects.filter(lecture=enrollment.lecture)
    try:
        from apps.domains.lectures.models import SectionAssignment

        section_assignment = SectionAssignment.objects.filter(enrollment=enrollment).first()
        if section_assignment and section_assignment.class_section_id:
            session_qs = session_qs.filter(section_id=section_assignment.class_section_id)
    except Exception:
        pass
    return list(session_qs.order_by("order"))


def unresolved_auto_clinic_session_ids(*, tenant: Any, enrollment_id: int) -> set[int]:
    from apps.domains.progress.models import ClinicLink

    return set(
        ClinicLink.objects.filter(
            enrollment_id=enrollment_id,
            is_auto=True,
            resolved_at__isnull=True,
            session__lecture__tenant=tenant,
        ).values_list("session_id", flat=True)
    )
