"""Read the actual lesson roster and attendance for one operating day."""
from django.db.models import Exists, F, OuterRef, Q
from rest_framework.exceptions import NotFound
from rest_framework.fields import DateField, IntegerField

from apps.domains.attendance.models import Attendance
from apps.domains.enrollment.models import SessionEnrollment
from apps.domains.lectures.models import Session, SessionSchedule
from apps.domains.students.models import ClassGroup


def daily_attendance(tenant, params):
    day = DateField().run_validation(params.get("date"))
    selected = None
    if "group" in params:
        selected = IntegerField(min_value=0, max_value=2**63 - 1).run_validation(params["group"])
    groups = dict(ClassGroup.objects.filter(tenant=tenant).order_by("name", "pk").values_list("pk", "name"))
    if selected and selected not in groups:
        raise NotFound()
    groups[0] = "미편성 강의"
    schedules = SessionSchedule.objects.filter(session_id=OuterRef("pk"))
    lessons = Session.objects.filter(lecture__tenant=tenant, lecture__is_system=False).annotate(
        has_schedules=Exists(schedules), teaches_today=Exists(schedules.filter(date=day)),
    ).filter(Q(has_schedules=False, date=day) | Q(teaches_today=True)).order_by("lecture__title", "lecture_id", "order", "pk").values_list(
        "pk", "order", "title", "lecture_id", "lecture__title", "lecture__class_group_link__group_id", "has_schedules",
    )
    summaries = {}
    lesson_rows = {}
    scheduled = set()
    for pk, order, title, course, course_title, group, has_schedules in lessons:
        if group is not None and group not in groups:
            continue
        group = group or 0
        if selected is not None and group != selected:
            continue
        if has_schedules:
            scheduled.add(pk)
        summary = summaries.setdefault(group, {
            "id": group, "name": groups[group], "lesson_count": 0, "total": 0,
            "missing": 0, "unset": 0, "marked": 0, "absent": 0,
        })
        summary["lesson_count"] += 1
        lesson_rows[pk] = (group, {
            "id": pk, "order": order, "title": title, "course_id": course, "course": course_title,
            "students": [],
        })
    scope = {
        "tenant": tenant, "session_id__in": lesson_rows.keys(), "enrollment__tenant": tenant,
        "enrollment__lecture_id": F("session__lecture_id"), "enrollment__student__tenant": tenant,
        "enrollment__student__deleted_at__isnull": True,
    }
    fields = ["session_id", "enrollment_id"]
    if selected is not None:
        fields.append("enrollment__student__name")
    # A roster entry can precede its attendance record. Preserve recorded history
    # even when a legacy attendance row has no separate session enrollment.
    units = {}
    roster = SessionEnrollment.objects.filter(**scope).filter(
        ~Q(session_id__in=scheduled) | Q(schedule_membership__schedule__date=day,
                                         schedule_membership__schedule__session_id=F("session_id")),
    )
    for session, enrollment, *name in roster.values_list(*fields):
        units[session, enrollment] = (None, name)
    attendance = Attendance.objects.filter(**scope).values_list(*fields[:2], "status", *fields[2:])
    for session, enrollment, status, *name in attendance:
        if session not in scheduled or (session, enrollment) in units:
            units[session, enrollment] = (status, name)
    for (session, enrollment), (status, name) in units.items():
        group, lesson = lesson_rows[session]
        summary = summaries[group]
        summary["total"] += 1
        if status is None:
            summary["missing"] += 1
        elif status == "UNSET":
            summary["unset"] += 1
        else:
            summary["marked"] += 1
            summary["absent"] += int(status == "ABSENT")
        if selected is not None:
            lesson["students"].append({"enrollment": enrollment, "name": name[0], "status": status})
    detail = []
    if selected is not None:
        for _, lesson in lesson_rows.values():
            lesson["students"].sort(key=lambda student: (student["name"], student["enrollment"]))
            detail.append(lesson)
    return {
        "date": day.isoformat(), "groups": [summaries[group] for group in groups if group in summaries],
        "group_name": groups.get(selected), "lessons": detail,
        "status_labels": dict(Attendance._meta.get_field("status").choices),
    }
