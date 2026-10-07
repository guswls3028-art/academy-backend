"""Current learning projections; never revoke access to stored learning history."""

from datetime import date
from typing import Any

from django.db.models import Q


def ongoing_lecture_filter(*, today: date, prefix: str = "lecture") -> Q:
    return Q(**{
        f"{prefix}__is_active": True,
        f"{prefix}__is_system": False,
    }) & (
        Q(**{f"{prefix}__end_date__isnull": True})
        | Q(**{f"{prefix}__end_date__gte": today})
    )


def lecture_is_ongoing(lecture: Any, *, today: date) -> bool:
    return bool(
        lecture.is_active
        and not lecture.is_system
        and (lecture.end_date is None or lecture.end_date >= today)
    )


def lecture_has_current_learning_todos(lecture: Any, *, today: date) -> bool:
    return lecture_is_ongoing(lecture, today=today) and (
        lecture.start_date is None or lecture.start_date <= today
    )
