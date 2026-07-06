"""Cross-domain dependencies for homework views."""

from __future__ import annotations

from typing import Any


def get_homework_for_assignment(*, homework_id: int, tenant: Any) -> Any:
    from apps.domains.homework_results.models import Homework

    return (
        Homework.objects.select_related(
            "session",
            "session__lecture",
        )
        .exclude(
            meta__removed_from_session_at__isnull=False,
        )
        .get(
            id=homework_id,
            session__lecture__tenant=tenant,
        )
    )


def active_enrollment_ids_for_session(*, tenant: Any, session_id: int) -> set[int]:
    from apps.domains.enrollment.selectors import active_enrollment_ids_for_session as _active_ids

    return _active_ids(tenant=tenant, session_id=session_id)


def active_session_enrollments_for_session(*, tenant: Any, session_id: int):
    from apps.domains.enrollment.selectors import active_session_enrollments_for_session as _active_rows

    return _active_rows(tenant=tenant, session_id=session_id)


def session_exists_for_tenant(*, session_id: int, tenant: Any) -> bool:
    from apps.domains.lectures.models import Session

    return Session.objects.filter(id=session_id, lecture__tenant=tenant).exists()


def recalc_scores_for_policy_change(*, policy: Any) -> None:
    from apps.domains.homework_results.services.policy_recalc import recalc_scores_for_policy_change as _recalc

    _recalc(policy=policy)
