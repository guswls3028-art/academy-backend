"""Cross-domain dependencies for attendance roster services."""

from __future__ import annotations

from typing import Any


def require_attendance_tenant(tenant: Any) -> Any:
    from apps.domains.enrollment.selectors import require_tenant

    return require_tenant(tenant)


def active_student_ids_for_tenant(*, tenant: Any, student_ids: list[int]) -> set[int]:
    from apps.domains.students.selectors import students_for_tenant

    return set(
        students_for_tenant(tenant, deleted="active")
        .filter(id__in=student_ids)
        .values_list("id", flat=True)
    )


def auto_assign_roster_fees(*, tenant: Any, student: Any, lecture: Any, enrollment: Any) -> None:
    from apps.domains.fees.services import auto_assign_fees_on_enrollment

    auto_assign_fees_on_enrollment(
        tenant,
        student,
        lecture,
        enrollment,
    )
