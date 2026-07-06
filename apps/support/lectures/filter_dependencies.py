"""Cross-domain model dependencies for lecture filters."""

from __future__ import annotations


def get_enrollment_model():
    from apps.domains.enrollment.models import Enrollment

    return Enrollment


def get_attendance_model():
    from apps.domains.attendance.models import Attendance

    return Attendance


def active_enrollment_count_for_lecture(*, lecture_id: int) -> int:
    from apps.domains.enrollment.models import Enrollment

    return Enrollment.objects.filter(
        lecture_id=lecture_id,
        status="ACTIVE",
    ).count()
