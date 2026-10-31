"""QR writes share canonical records and serialize with student lifecycle writes."""

from datetime import timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied

from apps.domains.attendance.models import Attendance, AttendanceQrPoint
from apps.support.attendance.learning_todo_eligibility import reconcile_learning_todo_targets
from apps.support.attendance.qr_dependencies import (
    Enrollment, SessionEnrollment, SessionParticipant, Student, Lecture,
    booking_window, session_window, change_participant_status, checkout_participant,
)

KST = ZoneInfo("Asia/Seoul")


class QrConflict(APIException):
    status_code = 409
    default_detail = "출결 상태가 변경되었습니다. QR을 다시 스캔해 주세요."


def get_point(*, tenant, token, locked=False):
    points = AttendanceQrPoint.objects.filter(tenant=tenant, token=token, active=True)
    if locked:
        points = points.select_for_update()
    point = points.first()
    if point is None:
        raise NotFound("이 학원의 유효한 출결 QR이 아닙니다. 게시된 최신 QR을 확인해 주세요.")
    if point.kind == "class" and not Lecture.objects.filter(
        pk=point.lecture_id, tenant=tenant, is_active=True, is_system=False,
    ).exists():
        raise NotFound("현재 운영 중인 수업의 QR이 아닙니다.")
    return point


def class_roster(*, tenant, student, point, now):
    return SessionEnrollment.objects.filter(
        tenant=tenant, enrollment__tenant=tenant, enrollment__student=student,
        enrollment__student__tenant=tenant, enrollment__status="ACTIVE",
        enrollment__lecture_id=point.lecture_id, session__lecture_id=point.lecture_id,
        session__lecture__tenant=tenant, session__date=now.date(),
    ).filter(
        Q(session__section__isnull=True)
        | Q(session__section__lecture_id=F("session__lecture_id"), session__section__tenant=tenant, session__section__is_active=True)
    ).select_related("session", "session__section", "enrollment").order_by("session__order", "id")


def clinic_candidates(*, tenant, student, point, now):
    rows = SessionParticipant.objects.filter(
        tenant=tenant, student=student, student__tenant=tenant,
        session__tenant=tenant, session__date__range=(now.date() - timedelta(days=1), now.date()),
        status__in=["booked", "attended"],
    ).filter(
        Q(enrollment__isnull=True) | Q(
            enrollment__tenant=tenant, enrollment__student=student,
            enrollment__lecture__tenant=tenant, enrollment__status="ACTIVE",
        ),
    ).select_related("session").order_by("session__start_time", "id")
    if point.location:
        rows = rows.filter(session__location=point.location)
    for row in rows:
        # The canonical booking window also handles sessions crossing midnight.
        start, end = session_window(row.session)
        if row.booking_start_time and row.booking_end_time:
            start, end = booking_window(
                session=row.session, start_time=row.booking_start_time, end_time=row.booking_end_time,
            )
        local_now = now.replace(tzinfo=None)
        if start - timedelta(minutes=60) <= local_now <= end + timedelta(hours=3):
            yield row


def resolve(*, tenant, student, point, now=None):
    now = now or timezone.now().astimezone(KST)
    choices = []
    if point.kind == "class":
        for membership in class_roster(tenant=tenant, student=student, point=point, now=now):
            row = Attendance.objects.filter(
                tenant=tenant, session=membership.session, enrollment=membership.enrollment,
            ).first()
            status = row.status if row else "UNSET"
            choices.append({
                "id": membership.session_id, "label": membership.session.title,
                "date": membership.session.date, "start_time": None,
                "status": status, "checked_in_at": row.qr_checked_in_at if row else None,
                "checked_out_at": None, "can_check_in": status in ("UNSET", "PRESENT", "LATE"),
                "can_check_out": False,
            })
    else:
        for row in clinic_candidates(tenant=tenant, student=student, point=point, now=now):
            choices.append({
                "id": row.pk, "label": row.session.title or "클리닉",
                "date": row.session.date, "start_time": row.booking_start_time or row.session.start_time,
                "status": row.status, "checked_in_at": row.checked_in_at,
                "checked_out_at": row.checked_out_at,
                "can_check_in": row.status == "booked" and row.checked_out_at is None,
                "can_check_out": row.status == "attended" and row.checked_in_at is not None,
            })
    return {"point": {"label": point.label, "kind": point.kind}, "choices": choices}


@transaction.atomic
def record(*, tenant, student, actor, token, target_id, action):
    # Same lock ordering as manual attendance and enrollment/withdrawal.
    student = Student.objects.select_for_update().filter(
        pk=student.pk, tenant=tenant, user=actor, deleted_at__isnull=True,
    ).first()
    if student is None:
        raise PermissionDenied("활성 학생 본인 계정으로 로그인해 주세요.")
    point = get_point(tenant=tenant, token=token, locked=True)
    now = timezone.now().astimezone(KST)
    if point.kind == "class":
        if action != "check_in":
            raise QrConflict("수업 QR은 출석에만 사용할 수 있습니다.")
        membership = class_roster(tenant=tenant, student=student, point=point, now=now).filter(
            session_id=target_id,
        ).first()
        if membership is None:
            raise NotFound("오늘 등록된 수업만 출석할 수 있습니다.")
        enrollment = Enrollment.objects.select_for_update().get(pk=membership.enrollment_id)
        if not class_roster(tenant=tenant, student=student, point=point, now=now).filter(
            pk=membership.pk, session_id=target_id, enrollment_id=enrollment.pk,
        ).exists():
            raise QrConflict()
        row, _ = Attendance.objects.select_for_update().get_or_create(
            tenant=tenant, session_id=target_id, enrollment=enrollment,
        )
        already = row.status in ("PRESENT", "LATE")
        if row.status not in ("UNSET", "PRESENT", "LATE"):
            raise QrConflict("선생님이 입력한 출결은 QR로 변경할 수 없습니다. 선생님에게 확인해 주세요.")
        if not already:
            row.status = "PRESENT"
            row.qr_checked_in_at = now
            row.attended_section_id = membership.session.section_id
            row.save(update_fields=["status", "qr_checked_in_at", "attended_section"])
            reconcile_learning_todo_targets(row)
        checked_in, checked_out, status = row.qr_checked_in_at, None, row.status
    else:
        candidate = next((row for row in clinic_candidates(
            tenant=tenant, student=student, point=point, now=now,
        ) if row.pk == target_id), None)
        if candidate is None:
            raise NotFound("이 장소에서 지금 등원·하원할 수 있는 확정 예약이 없습니다.")
        row = SessionParticipant.objects.select_for_update().get(pk=candidate.pk, tenant=tenant)
        if row.student_id != student.pk or row.session_id != candidate.session_id:
            raise QrConflict()
        if not any(current.pk == row.pk for current in clinic_candidates(
            tenant=tenant, student=student, point=point, now=now,
        )):
            raise QrConflict()
        if action == "check_in":
            already = row.status == "attended" and row.checked_in_at is not None
            if not already:
                if row.status != "booked" or row.checked_out_at is not None:
                    raise QrConflict("현재 예약 상태에서는 등원할 수 없습니다.")
                row = change_participant_status(
                    tenant=tenant, participant_id=row.pk, next_status="attended", actor=actor,
                ).participant
        else:
            already = row.checked_out_at is not None
            if row.status != "attended" or row.checked_in_at is None:
                raise QrConflict("먼저 등원을 기록해 주세요.")
            row = checkout_participant(
                tenant=tenant, participant_id=row.pk, actor=actor,
                expected_session_id=candidate.session_id, expected_student_id=student.pk,
            ).participant
        # QR records attendance only. Notification sending remains the staff-owned flow;
        # never dispatch a student-supplied recipient/template or mark study complete.
        checked_in, checked_out, status = row.checked_in_at, row.checked_out_at, row.status
    label = "하원" if action == "check_out" else ("등원" if point.kind == "clinic" else "출석")
    return {
        "detail": f"이미 {label} 기록이 있습니다." if already else f"{label}이 기록되었습니다.",
        "already_recorded": already, "kind": point.kind, "target_id": target_id,
        "status": status, "recorded_at": checked_out if action == "check_out" else checked_in,
        "checked_in_at": checked_in, "checked_out_at": checked_out,
    }
