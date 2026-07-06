"""Cross-domain dependencies for homework result views."""

from __future__ import annotations

from typing import Any


def get_teacher_or_admin_permission() -> type:
    from apps.domains.results.permissions import IsTeacherOrAdmin

    return IsTeacherOrAdmin


def get_session_for_homework(*, session_id: int, tenant: Any, for_update: bool = False) -> Any | None:
    from apps.domains.lectures.models import Session

    queryset = Session.objects
    if for_update:
        queryset = queryset.select_for_update()
    return queryset.filter(id=session_id, lecture__tenant=tenant).first()


def delete_homework_assignments(*, tenant: Any, homework: Any) -> int:
    from apps.domains.homework.models import HomeworkAssignment

    deleted_count, _ = HomeworkAssignment.objects.filter(
        tenant=tenant,
        homework=homework,
    ).delete()
    return int(deleted_count)
