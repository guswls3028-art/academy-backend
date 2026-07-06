"""Cross-domain dependencies for fees views."""

from __future__ import annotations

from typing import Any


def get_request_student(request: Any) -> Any | None:
    from apps.domains.student_app.permissions import get_request_student as _get_request_student

    return _get_request_student(request)


def active_student_ids_for_tenant(*, tenant: Any, student_ids: list[int]) -> set[int]:
    from apps.domains.students.models import Student

    return set(
        Student.objects.filter(
            id__in=student_ids,
            tenant=tenant,
            deleted_at__isnull=True,
        ).values_list("id", flat=True)
    )
