"""Staff dispositions are shared by the authorized staff of an enrollment, per evidence occurrence."""
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.fields import ChoiceField

from apps.domains.enrollment.models import Enrollment
from apps.domains.students.models import ClassGroup, DawonAlertDisposition, Student
from apps.domains.students.services.dawon_directory_search import active_students, compact, natural_name
from apps.domains.students.services.dawon_followup import alert_rows, positive
from apps.support.students.teacher_alert_evidence import DEFAULTS


def scoped_rows(tenant, links, group=None):
    if group:
        links = links.filter(group_id=positive(group))
    courses = {link.lecture_id: link for link in links.select_related("group", "lecture")}
    active = alert_rows(tenant, lecture_ids=courses) if courses else []
    by_key = {row["key"]: {**row, "status": "pending", "completed_at": None, "completed_by": ""}
              for row in active}
    saved = DawonAlertDisposition.objects.filter(
        enrollment__tenant=tenant, enrollment__lecture_id__in=courses, enrollment__status="ACTIVE",
        enrollment__student__in=active_students(Student.objects.all(), tenant),
    ).select_related("enrollment__student", "completed_by")
    for disposition in saved:
        enrollment = disposition.enrollment
        link = courses[enrollment.lecture_id]
        key = f"{enrollment.pk}:{disposition.rule}:{disposition.source}"
        row = by_key.setdefault(key, {"key": key, "group_id": link.group_id, "group_name": link.group.name,
            "lecture_id": link.lecture_id, "lecture_title": link.lecture.title,
            "student_id": enrollment.student_id, "student_name": enrollment.student.name, "rule": disposition.rule,
            "detail": disposition.detail, "value": disposition.value})
        row["status"] = "completed" if disposition.completed_at else "pending"
        row["completed_at"] = disposition.completed_at
        actor = disposition.completed_by
        row["completed_by"] = actor.name if actor and actor.tenant_id == tenant.pk else ""
    return list(by_key.values())


def queue_page(tenant, links, params):
    status = ChoiceField(choices=("pending", "completed")).run_validation(params.get("status", "pending"))
    page = positive(params.get("page", 1))
    size = 25
    selected = links
    for param, field in (("group", "group_id"), ("lecture_id", "lecture_id")):
        if params.get(param):
            selected = selected.filter(**{field: positive(params[param])})
    for param, choices in (("kind", ClassGroup.Kind.values), ("track", ClassGroup.Track.values)):
        if params.get(param):
            selected = selected.filter(**{"group__"+param: ChoiceField(choices=choices).run_validation(params[param])})
    rows = scoped_rows(tenant, selected)
    areas = {
        "attendance": {"absence_streak"},
        "exam": {"exam_drop", "recent_exam_drop", "top_reference_gap", "exam_rise"},
        "homework": {"homework_missing", "homework_drop"},
        "video": {"video_watch"},
    }
    if params.get("area"):
        area = ChoiceField(choices=tuple(areas)).run_validation(params["area"])
        rows = [row for row in rows if row["rule"] in areas[area]]
    if params.get("rule"):
        rule = ChoiceField(choices=DEFAULTS).run_validation(params["rule"])
        rows = [row for row in rows if row["rule"] == rule]
    term = compact(params.get("q", ""))[:100]
    if term:
        rows = [row for row in rows if any(term in compact(row[key]) for key in ("group_name", "student_name", "lecture_title"))]
    counts = {state: sum(row["status"] == state for row in rows) for state in ("pending", "completed")}
    rows = [row for row in rows if row["status"] == status]
    rows.sort(key=lambda row: (natural_name(row["group_name"]), natural_name(row["student_name"]), row["lecture_id"], row["rule"], row["key"]))
    if status == "completed":
        rows.sort(key=lambda row: row["completed_at"], reverse=True)
    return {"counts":counts, "count":len(rows), "alerts":rows[(page-1)*size:page*size]}


@transaction.atomic
def update_disposition(tenant, user, links, payload):
    if set(payload) != {"key", "status"} or not isinstance(payload["key"], str):
        raise ValidationError("경보와 처리 상태를 선택하세요.")
    status = ChoiceField(choices=("pending", "completed")).run_validation(payload["status"])
    parts = payload["key"].split(":")
    if len(parts) != 3 or parts[1] not in DEFAULTS:
        raise ValidationError("경보를 확인하세요.")
    eid, rule, source = positive(parts[0]), parts[1], positive(parts[2])
    enrollment = Enrollment.objects.select_for_update().filter(
        pk=eid, tenant=tenant, lecture_id__in=links.values("lecture_id"), status="ACTIVE",
        student__in=active_students(Student.objects.all(), tenant),
    ).first()
    if not enrollment:
        raise NotFound()
    saved = DawonAlertDisposition.objects.filter(enrollment=enrollment, rule=rule, source=source).first()
    if saved is None:
        signal = next((row for row in alert_rows(tenant, lecture_ids=[enrollment.lecture_id])
                       if row["key"] == payload["key"]), None)
        if signal is None:
            raise NotFound("현재 조회할 수 있는 경보가 아닙니다.")
        if status == "pending":
            return
        saved = DawonAlertDisposition(enrollment=enrollment, rule=rule, source=source,
                                     detail=signal["detail"], value=signal["value"])
    elif bool(saved.completed_at) == (status == "completed"):
        return
    saved.completed_at = timezone.now() if status == "completed" else None
    saved.completed_by = user if saved.completed_at else None
    saved.save()
