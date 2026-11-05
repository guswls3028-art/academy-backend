"""Optional teaching dates for one shared lesson, with atomic roster assignment."""
import hashlib
import json
from datetime import timedelta
from uuid import uuid4

from django.db import transaction
from django.db.models import F
from rest_framework import serializers
from rest_framework.exceptions import APIException, NotFound, ValidationError

from apps.support.lectures.schedule_dependencies import Attendance, SessionEnrollment, bulk_create_session_enrollments
from ..models import Session, SessionSchedule, SessionScheduleChange, SessionScheduleMembership


class ScheduleConflict(APIException):
    status_code = 409
    default_detail = "수업일 또는 학생 명단이 변경되었습니다. 최신 내용을 불러온 뒤 다시 저장해 주세요."


class ScheduleId(serializers.IntegerField):
    def to_internal_value(self, data):
        if isinstance(data, bool) or not isinstance(data, int) or not 0 < data <= 2**63 - 1:
            self.fail("invalid")
        return data


class ScheduleInput(serializers.Serializer):
    id = ScheduleId(required=False, allow_null=True)
    label = serializers.CharField(max_length=40)
    date = serializers.DateField()
    start_time = serializers.TimeField(required=False, allow_null=True)
    end_time = serializers.TimeField(required=False, allow_null=True)
    enrollment_ids = serializers.ListField(child=ScheduleId(), max_length=2000)

    def validate(self, attrs):
        start, end = attrs.get("start_time"), attrs.get("end_time")
        if any(value and (value.second or value.microsecond) for value in (start, end)):
            raise ValidationError("시간은 분 단위로 입력해 주세요.")
        if end and (not start or end <= start):
            raise ValidationError("종료 시간은 시작 시간보다 뒤여야 합니다.")
        attrs.setdefault("start_time", None)
        attrs.setdefault("end_time", None)
        return attrs


def roster_for(session):
    return SessionEnrollment.objects.filter(
        session=session, tenant_id=session.lecture.tenant_id,
        enrollment__tenant_id=session.lecture.tenant_id,
        enrollment__lecture_id=session.lecture_id,
        enrollment__student__tenant_id=session.lecture.tenant_id,
        enrollment__student__deleted_at__isnull=True,
    ).select_related("enrollment__student").order_by("enrollment_id")


def schedule_state(session):
    roster = list(roster_for(session))
    schedule_rows = list(session.schedules.all())
    members = SessionScheduleMembership.objects.filter(
        schedule__session=session, session_enrollment_id__in=[r.id for r in roster],
    ).values_list("schedule_id", "session_enrollment__enrollment_id")
    grouped = {}
    for schedule_id, enrollment_id in members:
        grouped.setdefault(schedule_id, []).append(enrollment_id)
    schedules = [{
        "id": row.id, "label": row.label, "date": row.date.isoformat(),
        "start_time": row.start_time.strftime("%H:%M") if row.start_time else None,
        "end_time": row.end_time.strftime("%H:%M") if row.end_time else None,
        "enrollment_ids": sorted(grouped.get(row.id, [])),
    } for row in schedule_rows]
    assigned = {eid for ids in grouped.values() for eid in ids}
    payload = {
        "schedules": schedules,
        "roster": [{"enrollment_id": r.enrollment_id, "student_name": r.enrollment.student.name} for r in roster],
        "unassigned_enrollment_ids": [r.enrollment_id for r in roster if r.enrollment_id not in assigned],
    }
    payload["version"] = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return payload


@transaction.atomic
def save_schedules(*, session, data, actor_id, source_session=None):
    if not isinstance(data, dict):
        raise ValidationError("수업일 설정을 다시 불러와 주세요.")
    session = Session.objects.select_for_update().select_related("lecture").get(pk=session.pk)
    before = schedule_state(session)
    if data.get("version") != before["version"]:
        raise ScheduleConflict()
    rows = data.get("schedules")
    if source_session is not None:
        # Copy explicitly requested active memberships only. Past attendance and scores stay put.
        source = Session.objects.filter(pk=source_session, lecture=session.lecture).first()
        if source is None or source.pk == session.pk:
            raise ValidationError("같은 강의의 다른 차시를 선택해 주세요.")
        source_state = schedule_state(source)
        if not source_state["schedules"]:
            raise ValidationError("선택한 차시에 가져올 수업일이 없습니다.")
        if before["schedules"]:
            raise ValidationError("기존 수업일을 덮어쓰지 않도록, 수업일이 없는 차시에서만 가져올 수 있습니다.")
        removed = Attendance.objects.filter(tenant=session.lecture.tenant, session=session, status="SECESSION").values_list("enrollment_id", flat=True)
        eligible = set(roster_for(source).filter(enrollment__status="ACTIVE").exclude(enrollment_id__in=removed).values_list("enrollment_id", flat=True))
        existing = {r["enrollment_id"] for r in before["roster"]}
        missing = sorted(set(eligible) - existing)
        for offset in range(0, len(missing), 200):
            bulk_create_session_enrollments(tenant=session.lecture.tenant, session_id=session.pk, enrollment_ids=missing[offset:offset + 200])
        delta = session.date - source.date if session.date and source.date else timedelta(days=7)
        rows = [{**row, "id": None,
                 "date": (serializers.DateField().to_internal_value(row["date"]) + delta).isoformat(),
                 "enrollment_ids": [eid for eid in row["enrollment_ids"] if eid in eligible]}
                for row in source_state["schedules"]]
    if not isinstance(rows, list) or len(rows) > 20:
        raise ValidationError("수업일은 최대 20개까지 등록할 수 있습니다.")
    validator = ScheduleInput(data=rows, many=True)
    validator.is_valid(raise_exception=True)
    rows = validator.validated_data
    labels = [row["label"] for row in rows]
    ids = [row["id"] for row in rows if row.get("id")]
    all_ids = [eid for row in rows for eid in row["enrollment_ids"]]
    if len(labels) != len(set(labels)) or len(ids) != len(set(ids)):
        raise ValidationError("수업일 이름 또는 항목이 중복되었습니다.")
    if len(all_ids) != len(set(all_ids)):
        raise ValidationError("학생은 같은 차시의 수업일 한 곳에만 배정할 수 있습니다.")
    roster = {r.enrollment_id: r for r in roster_for(session).select_for_update(of=("self",))}
    if set(all_ids) - roster.keys():
        raise ValidationError("현재 차시 명단에 없는 학생이 포함되어 있습니다. 명단을 다시 불러와 주세요.")
    existing = {row.id: row for row in session.schedules.all()}
    if set(ids) - existing.keys():
        raise ValidationError("현재 차시에 속하지 않는 수업일입니다.")
    # Renaming two labels in one request must not hit the uniqueness constraint midway.
    occupied = set(labels) | {row.label for row in existing.values()}
    for pk in existing:
        temporary = uuid4().hex
        while temporary in occupied:
            temporary = uuid4().hex
        occupied.add(temporary)
        SessionSchedule.objects.filter(pk=pk).update(label=temporary)
    SessionScheduleMembership.objects.filter(
        schedule__session=session, session_enrollment_id__in=[r.pk for r in roster.values()],
    ).delete()
    retained = []
    memberships = []
    for row in rows:
        row = dict(row)
        pk = row.pop("id", None)
        enrollment_ids = row.pop("enrollment_ids")
        schedule = existing.get(pk) if pk else None
        if schedule is None:
            schedule = SessionSchedule.objects.create(session=session, **row)
        else:
            for key, value in row.items():
                setattr(schedule, key, value)
            schedule.save()
        retained.append(schedule.id)
        memberships.extend(SessionScheduleMembership(schedule=schedule, session_enrollment=roster[eid]) for eid in enrollment_ids)
    session.schedules.exclude(id__in=retained).delete()
    SessionScheduleMembership.objects.bulk_create(memberships)
    after = schedule_state(session)
    SessionScheduleChange.objects.create(session=session, actor_id=actor_id, before=before, after=after)
    return after


def schedule_enrollment_ids(*, tenant, session_id, value):
    """None means all; 0 explicitly selects unassigned. Invalid scope never means all."""
    if value is None:
        return None
    if not session_id:
        raise ValidationError({"session": "수업일을 조회할 차시를 선택해 주세요."})
    if isinstance(value, bool) or not str(value).isascii() or not str(value).isdigit():
        raise ValidationError({"schedule": "수업일을 다시 선택해 주세요."})
    schedule_id = int(value)
    if schedule_id > 2**63 - 1:
        raise ValidationError({"schedule": "수업일을 다시 선택해 주세요."})
    roster = SessionEnrollment.objects.filter(
        tenant=tenant, session_id=session_id, session__lecture__tenant=tenant,
        enrollment__tenant=tenant, enrollment__student__tenant=tenant,
        enrollment__lecture_id=F("session__lecture_id"),
    )
    if schedule_id == 0:
        return roster.filter(schedule_membership__isnull=True).values_list("enrollment_id", flat=True)
    if not SessionSchedule.objects.filter(id=schedule_id, session_id=session_id, session__lecture__tenant=tenant).exists():
        raise NotFound("수업일을 찾을 수 없습니다. 최신 목록을 불러와 주세요.")
    return roster.filter(schedule_membership__schedule_id=schedule_id).values_list("enrollment_id", flat=True)
