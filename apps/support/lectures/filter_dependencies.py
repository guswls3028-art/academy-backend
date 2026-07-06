"""Cross-domain model dependencies for lecture filters."""

from __future__ import annotations


def get_enrollment_model():
    from apps.domains.enrollment.models import Enrollment

    return Enrollment


def get_attendance_model():
    from apps.domains.attendance.models import Attendance

    return Attendance
