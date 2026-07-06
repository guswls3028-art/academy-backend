"""Cross-domain dependencies for student result services."""

from __future__ import annotations

from typing import Any


def get_request_student(request: Any) -> Any | None:
    from apps.domains.student_app.permissions import get_request_student as _get_student

    return _get_student(request)


def active_enrollments_for_student(*, tenant: Any, student: Any):
    from apps.domains.enrollment.selectors import active_enrollments_for_student as _active_enrollments

    return _active_enrollments(tenant=tenant, student=student)
