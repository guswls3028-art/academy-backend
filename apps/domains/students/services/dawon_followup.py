from collections import defaultdict
from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.fields import DateField, IntegerField

from apps.domains.clinic.dawon_demo import LOCATION, MEMO
from apps.domains.clinic.models import Session as ClinicSession, SessionParticipant
from apps.domains.clinic.services.lifecycle import create_participants_bulk
from apps.domains.enrollment.models import Enrollment
from apps.domains.students.models import ClassGroupLecture, DawonCounselAppointment, DawonReportHomework, DawonTeacherAlertSettings
from apps.support.student_app.results_summary import build_student_grades_summary
from apps.support.students.teacher_alert_evidence import DEFAULTS, signals_for_lectures


def positive(value):
    return IntegerField(min_value=1,max_value=2**63-1).run_validation(value)


def links_for(tenant):
    return ClassGroupLecture.objects.filter(group__tenant=tenant, lecture__tenant=tenant, lecture__is_active=True)


def class_alerts(tenant, group):
    links = links_for(tenant)
    if group is not None:
        links = links.filter(group_id=positive(group))
    links = list(links.values("group_id","group__name","lecture_id","lecture__title","reference_teacher_id"))
    stored = dict(DawonTeacherAlertSettings.objects.filter(
        tenant=tenant,teacher_id__in={link["reference_teacher_id"] for link in links},
        teacher__tenant=tenant).values_list("teacher_id", "rules"))
    sets = defaultdict(dict)
    by_course = {}
    for link in links:
        saved = stored.get(link["reference_teacher_id"],{})
        settings = tuple((name, saved.get(name,{}).get("enabled",default["enabled"]),
                          saved.get(name,{}).get("threshold",default["threshold"])) for name, default in DEFAULTS.items())
        sets[settings][link["lecture_id"]] = link["lecture__title"]
        by_course[link["lecture_id"]] = link
    alerts = []
    for settings, courses in sets.items():
        rules = {key: {"enabled": enabled, "threshold": threshold} for key, enabled, threshold in settings}
        for row in signals_for_lectures(tenant,courses,timezone.localdate(),rules):
            link = by_course[row["lecture_id"]]
            alerts.append({**row, "group_id":link["group_id"], "group_name":link["group__name"]})
    alerts.sort(key=lambda r: (r["group_name"],r["student_name"],r["lecture_id"],r["rule"]))
    return {"count":len(alerts),"alerts":alerts}


def selected_enrollment(tenant, data):
    course = positive(data.get("course"))
    student = positive(data.get("student"))
    row = Enrollment.objects.filter(tenant=tenant,lecture_id=course,student_id=student,status="ACTIVE",
        lecture__tenant=tenant,lecture__is_active=True,student__tenant=tenant,student__deleted_at__isnull=True).select_related("student__user","lecture").first()
    if not row:
        raise NotFound()
    return row


def is_sample(student):
    if not student.user_id or student.user.tenant_id != student.tenant_id or student.user.is_staff or student.user.is_superuser:
        return False
    if student.pk == 2 and student.name == "다원 수업 검수 학생 2":
        return True
    suffix = (student.ps_number or "").removeprefix("DW-R-")
    return (len(suffix) == 2 and suffix.isascii() and suffix.isdigit() and int(suffix) < 32
            and student.ps_number == "DW-R-" + suffix and student.user.username == "t719_demo-report-student-" + suffix and not student.user.has_usable_password())


def followup_allowed(tenant, row):
    return (row.student.is_managed and is_sample(row.student)
            and links_for(tenant).filter(lecture=row.lecture, group__students=row.student).exists())


def slots_for(tenant):
    return ClinicSession.objects.filter(tenant=tenant,memo=MEMO,location=LOCATION,date__gte=timezone.localdate()).order_by("date","start_time","pk")


def followups(tenant, lecture_ids):
    appointments = DawonCounselAppointment.objects.filter(enrollment__tenant=tenant,enrollment__student__tenant=tenant,
        enrollment__lecture__tenant=tenant,enrollment__lecture_id__in=lecture_ids).order_by("-day","-pk")
    rows = [{"kind":"counsel","day":row.day,"student":row.enrollment.student.name,"course":row.enrollment.lecture.title}
            for row in appointments.select_related("enrollment__student","enrollment__lecture")]
    clinic = SessionParticipant.objects.filter(tenant=tenant,session__tenant=tenant,session__memo=MEMO,
        session__location=LOCATION,enrollment__tenant=tenant,enrollment__lecture_id__in=lecture_ids,
        student_id=F("enrollment__student_id"),student__tenant=tenant,status="booked").select_related("student","session","enrollment__lecture")
    rows.extend({"kind":"clinic","day":p.session.date,"student":p.student.name,"course":p.enrollment.lecture.title} for p in clinic)
    return sorted(rows,key=lambda r: (r["day"],r["student"],r["kind"]),reverse=True)


def student_record(tenant, data):
    row = selected_enrollment(tenant,data)
    summary = build_student_grades_summary(tenant=tenant,student=row.student,include_grade_board_details=False)
    dates = dict(row.lecture.sessions.values_list("pk","date"))
    exams, homework = [], []
    for key, output in (("exams",exams),("homeworks",homework)):
        for item in summary[key]:
            sid = item.get("session_id")
            if item["enrollment_id"] == row.pk and sid in dates:
                output.append({"title":item["title"],"date":dates[sid],"score":item.get("total_score",item.get("score")),
                    "maximum":item.get("max_score"),"state":item.get("submission_state","")})
        output.sort(key=lambda item: (str(item["date"] or ""),item["title"]))
    for assignment in DawonReportHomework.objects.filter(
        enrollment=row, report__session__lecture=row.lecture, report__exam__tenant=tenant,
    ).select_related("report__session").order_by("report__session__date", "pk"):
        homework.append({
            "title": assignment.title, "date": assignment.report.session.date,
            "completion": assignment.completion, "wrong": assignment.wrong, "unfinished": assignment.unfinished,
            "score": None, "maximum": None, "state": "",
        })
    homework.sort(key=lambda item: str(item["date"] or ""))
    counsel = list(DawonCounselAppointment.objects.filter(enrollment=row).order_by("-day").values_list("day",flat=True))
    clinic = list(SessionParticipant.objects.filter(tenant=tenant,enrollment=row,student=row.student,
        session__tenant=tenant,session__memo=MEMO,session__location=LOCATION).order_by("-session__date").values(
            "session__date","session__title","status"))
    allowed = followup_allowed(tenant, row)
    return {"student":row.student.name,"course":row.lecture.title,"exams":exams,"homework":homework,
            "counsel":counsel,"clinic":clinic,"can_follow":allowed,"today":timezone.localdate(),
            "slots":list(slots_for(tenant).values("id","title","date","start_time")[:14]) if allowed else []}


@transaction.atomic
def register_followup(request):
    tenant, data = request.tenant, request.data
    row = selected_enrollment(tenant,data)
    if not followup_allowed(tenant, row):
        raise NotFound()
    kind = data.get("followup")
    if kind == "counsel" and set(data) == {"followup","student","course","day"}:
        day = DateField().run_validation(data["day"])
        today = timezone.localdate()
        if not today <= day <= today + timedelta(days=180):
            raise ValidationError("오늘부터 180일 이내의 상담 날짜를 선택하세요.")
        DawonCounselAppointment.objects.get_or_create(enrollment=row,day=day,defaults={"registered_by":request.user})
    elif kind == "clinic" and set(data) == {"followup","student","course","session"}:
        slot = slots_for(tenant).select_for_update().filter(pk=positive(data["session"])).first()
        if not slot:
            raise NotFound()
        exists = SessionParticipant.objects.filter(tenant=tenant,session=slot,student=row.student,enrollment=row,status="booked").exists()
        if not exists:
            create_participants_bulk(tenant=tenant,session_ids=[slot.pk],enrollment_ids=[row.pk])
    else:
        raise ValidationError("상담 또는 클리닉 일정을 선택하세요.")
    return {"saved":kind}
