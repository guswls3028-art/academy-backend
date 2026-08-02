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


def get_homework_raw_score_cutline(
    *,
    session: Any,
    homework: Any | None = None,
) -> float | None:
    from apps.domains.homework.models import HomeworkPolicy

    if homework is not None:
        homework_mode = getattr(homework, "cutline_mode", None)
        homework_value = getattr(homework, "cutline_value", None)
        if homework_mode is not None and homework_value is not None:
            return float(homework_value) if homework_mode == "COUNT" else None

    policy = HomeworkPolicy.objects.filter(
        tenant_id=session.lecture.tenant_id,
        session=session,
    ).first()
    if policy is None or policy.cutline_mode != HomeworkPolicy.CutlineMode.COUNT:
        return None
    return float(policy.cutline_value)


def resolve_homework_cutline_settings(
    *,
    homework: Any,
    create_policy: bool = False,
) -> Any:
    from apps.domains.homework.utils.homework_policy import (
        resolve_homework_cutline_settings as resolve,
    )

    return resolve(
        session=homework.session,
        homework=homework,
        create_policy=create_policy,
    )
