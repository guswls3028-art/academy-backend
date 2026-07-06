"""Cross-domain read helpers for assignment-not-submitted notifications."""

from __future__ import annotations

from datetime import date
from typing import Any


def sessions_for_assignment_not_submitted(
    *,
    target_date: date,
    tenant_id: int | None = None,
) -> list[Any]:
    from apps.domains.lectures.models import Session

    sessions = Session.objects.filter(date=target_date).select_related("lecture")
    if tenant_id:
        sessions = sessions.filter(lecture__tenant_id=tenant_id)
    return list(sessions)


def homeworks_for_session(session: Any) -> list[Any]:
    from apps.domains.homework_results.models import Homework

    return list(Homework.objects.filter(session=session))


def assignments_for_homework_session(*, homework: Any, session: Any) -> list[Any]:
    from apps.domains.homework.models import HomeworkAssignment

    return list(
        HomeworkAssignment.objects.filter(
            homework=homework,
            session=session,
        ).select_related("enrollment__student")
    )


def first_attempt_homework_is_not_submitted(*, homework: Any, enrollment: Any) -> bool:
    from apps.domains.homework_results.models import HomeworkScore

    homework_score = HomeworkScore.objects.filter(
        homework=homework,
        enrollment=enrollment,
        attempt_index=1,
    ).first()

    if not homework_score:
        return True
    if homework_score.score is not None:
        return False

    meta = homework_score.meta if isinstance(homework_score.meta, dict) else {}
    return meta.get("status") != HomeworkScore.MetaStatus.NOT_SUBMITTED
