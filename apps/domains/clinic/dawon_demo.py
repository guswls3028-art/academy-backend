"""Native clinic sessions reserved for the isolated Dawon public sample."""

import re
from datetime import time, timedelta

from django.conf import settings
from django.utils import timezone

from .models import Session, SessionParticipant


MEMO = "dawon-public-review:clinic:v1"
LOCATION = "다원수학 클리닉 체험실"
SAMPLE_STUDENT_ID = 2
PARTICIPANT_ACTION = re.compile(r"/api/v1/clinic/participants/([1-9]\d{0,17})/(set_status|change-booking)/")
BOOKING_FIELDS = {"student_request_memo", "preferred_start_time", "preferred_end_time", "booking_start_time", "booking_end_time"}


def is_demo_tenant(tenant):
    return bool(getattr(settings, "DAWON_REVIEW_CLASS_GROUPS", False) and tenant and
                (tenant.pk, tenant.code) == (719, "dawon"))


def is_sample_session(session):
    return bool(getattr(settings, "DAWON_REVIEW_CLASS_GROUPS", False) and session and
                session.tenant_id == 719 and session.memo == MEMO and session.location == LOCATION)


def ensure_demo_clinic_sessions(tenant):
    if not is_demo_tenant(tenant):
        return
    today = timezone.localdate()
    dates = [today + timedelta(days=offset) for offset in range(1, 8)]
    present = set(Session.objects.filter(tenant=tenant, location=LOCATION, start_time=time(17),
                                         date__in=dates).values_list("date", flat=True))
    Session.objects.bulk_create([
        Session(tenant=tenant, location=LOCATION, memo=MEMO, date=day, start_time=time(17),
                title="수학 오답 클리닉", max_participants=12) for day in dates if day not in present
    ], ignore_conflicts=True)


def allows_demo_clinic_write(request, role):
    if not request.path.startswith("/api/v1/clinic/participants/"):
        return False
    tenant = getattr(request, "tenant", None)
    if not is_demo_tenant(tenant) or not isinstance(request.data, dict):
        return False
    data = request.data
    if data.get("student_request_memo"):
        return False
    sessions = Session.objects.filter(tenant=tenant, memo=MEMO, location=LOCATION)
    if role == "student" and request.method == "POST" and request.path == "/api/v1/clinic/participants/bulk-create/":
        ids = data.get("session_ids")
        return (set(data) <= BOOKING_FIELDS | {"session_ids"} and isinstance(ids, list) and len(ids) == 1
                and type(ids[0]) is int and 0 < ids[0] < 2**63 and sessions.filter(pk=ids[0]).exists())
    match = PARTICIPANT_ACTION.fullmatch(request.path)
    if not match:
        return False
    participant_id, action = match.groups()
    if action == "set_status" and request.method == "PATCH":
        status = data.get("status")
        if set(data) != {"status"} or not (
            (role == "teacher" and status in ("booked", "rejected")) or
            (role == "student" and status == "cancelled")
        ):
            return False
    elif action == "change-booking" and role == "student" and request.method == "POST":
        dest = data.get("new_session_id")
        if (set(data) - BOOKING_FIELDS - {"new_session_id"} or type(dest) is not int or
                not 0 < dest < 2**63 or not sessions.filter(pk=dest).exists()):
            return False
    else:
        return False
    participant = SessionParticipant.objects.filter(
        tenant=tenant, pk=participant_id, session__in=sessions, student_id=SAMPLE_STUDENT_ID,
        student__tenant=tenant, student__is_managed=True, student__deleted_at__isnull=True,
        student__name__in=("다원 수업 검수 학생 2", "김학생 2"), source=SessionParticipant.Source.STUDENT_REQUEST,
        **({"status": SessionParticipant.Status.PENDING} if role == "teacher" else {}),
    ).select_related("student__user").first()
    if not participant:
        return False
    from apps.core.dawon_preview_identity import matches_original
    return participant.student.name != "김학생 2" or matches_original(participant.student)
