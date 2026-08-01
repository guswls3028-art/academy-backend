"""Tenant-scoped read projection for upcoming supplement and clinic arrivals."""

from __future__ import annotations

from datetime import datetime, time, timedelta

from django.utils import timezone

from apps.domains.attendance.models import Attendance
from apps.support.attendance.arrival_dependencies import (
    clinic_arrival_participants_for_tenant,
)


SOON_WINDOW_MINUTES = 60
OVERVIEW_DAYS = 7
SUPPLEMENT_EXCLUDED_STATUSES = {"INACTIVE", "SECESSION"}
CLINIC_VISIBLE_STATUSES = {"booked", "attended", "no_show"}
CLINIC_RESOLVED_STATUSES = {"attended", "no_show"}


def _aware_datetime(date_value, time_value):
    if date_value is None or time_value is None:
        return None
    combined = datetime.combine(date_value, time_value)
    current_timezone = timezone.get_current_timezone()
    return timezone.make_aware(combined, current_timezone)


def _supplement_item(attendance: Attendance) -> dict:
    planned_date = attendance.planned_arrival_date
    planned_time = attendance.planned_arrival_time
    resolved = attendance.status != "UNSET"
    return {
        "key": f"supplement:{attendance.id}",
        "source": "supplement",
        "attendance_id": attendance.id,
        "clinic_participant_id": None,
        "clinic_session_id": None,
        "student_id": attendance.enrollment.student_id,
        "student_name": attendance.enrollment.student.name,
        "lecture_id": attendance.session.lecture_id,
        "lecture_title": attendance.session.lecture.title or attendance.session.lecture.name,
        "lecture_color": attendance.session.lecture.color,
        "session_id": attendance.session_id,
        "session_title": attendance.session.title,
        "date": planned_date,
        "time": planned_time,
        "location": "",
        "memo": attendance.memo or "",
        "status": attendance.status.lower(),
        "is_resolved": resolved,
    }


def _clinic_item(participant) -> dict:
    clinic_session = participant.session
    planned_date = clinic_session.date if clinic_session else participant.requested_date
    planned_time = clinic_session.start_time if clinic_session else participant.requested_start_time
    enrollment = participant.enrollment
    lecture = getattr(enrollment, "lecture", None) if enrollment else None
    return {
        "key": f"clinic:{participant.id}",
        "source": "clinic",
        "attendance_id": None,
        "clinic_participant_id": participant.id,
        "clinic_session_id": clinic_session.id if clinic_session else None,
        "student_id": participant.student_id,
        "student_name": participant.student.name,
        "lecture_id": lecture.id if lecture else None,
        "lecture_title": (lecture.title or lecture.name) if lecture else "",
        "lecture_color": lecture.color if lecture else "",
        "session_id": None,
        "session_title": clinic_session.title if clinic_session else "",
        "date": planned_date,
        "time": planned_time,
        "location": clinic_session.location if clinic_session else "",
        "memo": participant.memo or "",
        "status": participant.status,
        "is_resolved": participant.status in CLINIC_RESOLVED_STATUSES,
    }


def _serialize_item(item: dict, now) -> dict:
    planned_at = _aware_datetime(item["date"], item["time"])
    item["is_overdue"] = bool(
        planned_at is not None
        and planned_at < now
        and not item["is_resolved"]
    )
    item["date"] = item["date"].isoformat() if item["date"] else None
    item["time"] = item["time"].strftime("%H:%M") if item["time"] else None
    return item


def build_arrival_overview(*, tenant, now=None) -> dict:
    """Return the next seven days of operational arrivals in two queries."""
    current = timezone.localtime(now) if now is not None else timezone.localtime()
    today = current.date()
    tomorrow = today + timedelta(days=1)
    range_end = today + timedelta(days=OVERVIEW_DAYS - 1)

    supplement_rows = (
        Attendance.objects
        .filter(
            tenant=tenant,
            session__session_type="SUPPLEMENT",
            enrollment__student__deleted_at__isnull=True,
        )
        .exclude(status__in=SUPPLEMENT_EXCLUDED_STATUSES)
        .filter(planned_arrival_date__range=(today, range_end))
        .select_related("session", "session__lecture", "enrollment", "enrollment__student")
    )
    clinic_rows = clinic_arrival_participants_for_tenant(
        tenant=tenant,
        start_date=today,
        end_date=range_end,
        statuses=CLINIC_VISIBLE_STATUSES,
    )

    items = [
        *(_supplement_item(row) for row in supplement_rows),
        *(_clinic_item(row) for row in clinic_rows),
    ]
    items.sort(
        key=lambda item: (
            item["date"] or range_end,
            item["time"] or time.max,
            item["student_name"],
            item["key"],
        )
    )
    serialized_items = [_serialize_item(item, current) for item in items]

    soon_until = current + timedelta(minutes=SOON_WINDOW_MINUTES)
    soon = 0
    overdue = 0
    time_unset = 0
    for item in serialized_items:
        if item["is_overdue"]:
            overdue += 1
        if item["time"] is None and not item["is_resolved"]:
            time_unset += 1
        planned_at = _aware_datetime(
            datetime.fromisoformat(item["date"]).date() if item["date"] else None,
            time.fromisoformat(item["time"]) if item["time"] else None,
        )
        if (
            planned_at is not None
            and current <= planned_at <= soon_until
            and not item["is_resolved"]
        ):
            soon += 1

    return {
        "generated_at": current.isoformat(),
        "today": today.isoformat(),
        "tomorrow": tomorrow.isoformat(),
        "range_end": range_end.isoformat(),
        "range_days": OVERVIEW_DAYS,
        "soon_window_minutes": SOON_WINDOW_MINUTES,
        "summary": {
            "soon": soon,
            "today": sum(item["date"] == today.isoformat() for item in serialized_items),
            "tomorrow": sum(item["date"] == tomorrow.isoformat() for item in serialized_items),
            "upcoming": len(serialized_items),
            "time_unset": time_unset,
            "overdue": overdue,
        },
        "items": serialized_items,
    }
