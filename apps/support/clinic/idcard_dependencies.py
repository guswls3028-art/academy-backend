"""Cross-domain dependencies for the student clinic ID card API."""

from __future__ import annotations

from typing import Any


def student_for_idcard_user(*, tenant: Any, user: Any) -> Any | None:
    from apps.domains.students.selectors import student_for_tenant_user

    return student_for_tenant_user(tenant, user, deleted="active")


def active_enrollments_for_student(*, tenant: Any, student: Any) -> list[Any]:
    from apps.domains.enrollment.selectors import enrollments_for_tenant

    return list(
        enrollments_for_tenant(tenant)
        .filter(student=student, status="ACTIVE")
        .select_related("lecture")
        .order_by("lecture__title", "id")
    )


def ordered_sessions_by_enrollment(
    *,
    tenant: Any,
    enrollments: list[Any],
) -> dict[int, list[Any]]:
    if not enrollments:
        return {}

    from apps.domains.lectures.models import SectionAssignment
    from apps.domains.lectures.models import Session as LectureSession

    enrollment_ids = [int(enrollment.id) for enrollment in enrollments]
    lecture_ids = {int(enrollment.lecture_id) for enrollment in enrollments}
    section_by_enrollment = dict(
        SectionAssignment.objects.filter(
            tenant=tenant,
            enrollment_id__in=enrollment_ids,
        ).values_list("enrollment_id", "class_section_id")
    )
    sessions_by_lecture: dict[int, list[Any]] = {}
    for session in (
        LectureSession.objects.filter(
            lecture_id__in=lecture_ids,
            lecture__tenant=tenant,
        )
        .select_related("lecture")
        .order_by("lecture_id", "order")
    ):
        sessions_by_lecture.setdefault(int(session.lecture_id), []).append(session)

    result: dict[int, list[Any]] = {}
    for enrollment in enrollments:
        sessions = sessions_by_lecture.get(int(enrollment.lecture_id), [])
        section_id = section_by_enrollment.get(int(enrollment.id))
        if section_id:
            sessions = [
                session
                for session in sessions
                if int(session.section_id or 0) == int(section_id)
            ]
        result[int(enrollment.id)] = sessions
    return result


def unresolved_auto_clinic_links(
    *,
    tenant: Any,
    enrollment_ids: list[int],
) -> list[Any]:
    if not enrollment_ids:
        return []

    from apps.domains.progress.models import ClinicLink

    return list(
        ClinicLink.objects.filter(
            tenant=tenant,
            enrollment_id__in=enrollment_ids,
            is_auto=True,
            resolved_at__isnull=True,
            session__lecture__tenant=tenant,
        )
        .select_related("session__lecture")
        .order_by("session__lecture__title", "session__order", "id")
    )
