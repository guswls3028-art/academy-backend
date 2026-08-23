from __future__ import annotations

import datetime
import time
from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from apps.domains.clinic.models import (
    Session,
    SessionParticipant,
    SessionParticipantPlanItem,
)
from apps.support.clinic.session_dependencies import (
    active_enrolled_lecture_ids_for_student,
    cancel_pending_clinic_participant_reminders,
    clinic_enrollment_for_tenant,
    clinic_reason_for_unresolved_auto_links,
    locked_clinic_links_for_participant_plan,
    preferred_active_enrollment_id_for_student_session,
)


class Conflict(APIException):
    status_code = 409
    default_detail = "요청이 현재 데이터 상태와 충돌합니다."
    default_code = "conflict"


def cancel_active_participants_for_student(*, tenant, student, changed_at) -> int:
    return SessionParticipant.objects.filter(
        student=student,
        tenant=tenant,
        status__in=[SessionParticipant.Status.PENDING, SessionParticipant.Status.BOOKED],
    ).update(status=SessionParticipant.Status.CANCELLED, status_changed_at=changed_at)


@dataclass(frozen=True)
class ClinicNotificationEvent:
    trigger: str
    student: Any
    context: dict[str, Any]


@dataclass(frozen=True)
class ParticipantTransitionResult:
    participant: SessionParticipant
    notification: ClinicNotificationEvent | None = None


@dataclass(frozen=True)
class ParticipantWriteResult:
    participant: SessionParticipant
    notification: ClinicNotificationEvent | None = None


STAFF_STATUS_TRANSITIONS = {
    SessionParticipant.Status.PENDING: {
        SessionParticipant.Status.BOOKED,
        SessionParticipant.Status.REJECTED,
        SessionParticipant.Status.CANCELLED,
    },
    SessionParticipant.Status.BOOKED: {
        SessionParticipant.Status.ATTENDED,
        SessionParticipant.Status.NO_SHOW,
        SessionParticipant.Status.CANCELLED,
    },
    SessionParticipant.Status.ATTENDED: {
        SessionParticipant.Status.BOOKED,
        SessionParticipant.Status.NO_SHOW,
    },
    SessionParticipant.Status.NO_SHOW: {
        SessionParticipant.Status.BOOKED,
        SessionParticipant.Status.ATTENDED,
    },
    SessionParticipant.Status.REJECTED: set(),
    SessionParticipant.Status.CANCELLED: set(),
}

STUDENT_STATUS_TRANSITIONS = {
    SessionParticipant.Status.PENDING: {
        SessionParticipant.Status.CANCELLED,
    },
    SessionParticipant.Status.BOOKED: set(),
    SessionParticipant.Status.ATTENDED: set(),
    SessionParticipant.Status.NO_SHOW: set(),
    SessionParticipant.Status.REJECTED: set(),
    SessionParticipant.Status.CANCELLED: set(),
}

COMPLETE_ALLOWED_STATUSES = {
    SessionParticipant.Status.ATTENDED,
}

CLINIC_REMINDER_MIN_INTERVAL_MINUTES = 10
CLINIC_REMINDER_MAX_OCCURRENCES = 12
CLINIC_REMINDER_LATEST_TIME = datetime.time(22, 0)

SESSION_CHANGE_NOTICE_STATUSES = (
    SessionParticipant.Status.PENDING,
    SessionParticipant.Status.BOOKED,
    SessionParticipant.Status.ATTENDED,
    SessionParticipant.Status.NO_SHOW,
)


def _locked_participant(*, tenant, participant_id: int) -> SessionParticipant:
    try:
        return (
            SessionParticipant.objects
            .select_for_update()
            # `session` is nullable; joining it in the lock query makes PostgreSQL
            # reject FOR UPDATE on the nullable side of an outer join.
            .select_related("student")
            .get(pk=participant_id, tenant=tenant, student__deleted_at__isnull=True)
        )
    except SessionParticipant.DoesNotExist as exc:
        raise NotFound("예약을 찾을 수 없습니다.") from exc


def _plan_link_is_valid_for_participant(*, participant, clinic_link, target_lecture_ids) -> bool:
    enrollment = clinic_link.enrollment
    lecture_session = clinic_link.session
    return bool(
        participant.session_id
        and clinic_link.tenant_id == participant.tenant_id
        and clinic_link.is_auto
        and clinic_link.resolved_at is None
        and enrollment.tenant_id == participant.tenant_id
        and enrollment.student_id == participant.student_id
        and enrollment.status == "ACTIVE"
        and lecture_session.lecture_id == enrollment.lecture_id
        and (
            not target_lecture_ids
            or enrollment.lecture_id in target_lecture_ids
        )
    )


def planned_clinic_link_ids_for_participant(participant: SessionParticipant) -> list[int]:
    """Project only still-valid active plan rows; retain invalid rows for audit."""
    if not participant.session_id:
        return []
    target_lecture_ids = {
        int(lecture.id) for lecture in participant.session.target_lectures.all()
    }
    items = getattr(participant, "_active_plan_items", None)
    if items is None:
        items = list(
            SessionParticipantPlanItem.objects.filter(
                participant=participant,
                removed_at__isnull=True,
            ).select_related(
                "clinic_link__enrollment",
                "clinic_link__session",
            )
        )
    return sorted(
        {
            int(item.clinic_link_id)
            for item in items
            if _plan_link_is_valid_for_participant(
                participant=participant,
                clinic_link=item.clinic_link,
                target_lecture_ids=target_lecture_ids,
            )
        }
    )


@transaction.atomic
def replace_participant_clinic_plan(
    *,
    tenant,
    participant_id: int,
    clinic_link_ids,
    actor,
) -> SessionParticipant:
    """Replace the exact participant-scoped plan while retaining removal history."""
    try:
        normalized_ids = [int(link_id) for link_id in clinic_link_ids]
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            {"planned_clinic_link_ids": "ClinicLink ID는 양의 정수여야 합니다."}
        ) from exc
    if any(link_id <= 0 for link_id in normalized_ids):
        raise ValidationError(
            {"planned_clinic_link_ids": "ClinicLink ID는 양의 정수여야 합니다."}
        )
    if len(normalized_ids) != len(set(normalized_ids)):
        raise ValidationError(
            {"planned_clinic_link_ids": "중복 ClinicLink ID를 저장할 수 없습니다."}
        )
    normalized_ids = sorted(normalized_ids)

    participant = _locked_participant(tenant=tenant, participant_id=participant_id)
    if not participant.session_id:
        raise ValidationError(
            {"planned_clinic_link_ids": "확정된 클리닉 세션 참가자만 오늘 할 범위를 저장할 수 있습니다."}
        )
    participant.session = (
        Session.objects.filter(id=participant.session_id, tenant=tenant)
        .prefetch_related("target_lectures")
        .get()
    )
    target_lecture_ids = {
        int(lecture.id) for lecture in participant.session.target_lectures.all()
    }

    links = locked_clinic_links_for_participant_plan(
        clinic_link_ids=normalized_ids,
    )
    valid_ids = {
        int(link.id)
        for link in links
        if _plan_link_is_valid_for_participant(
            participant=participant,
            clinic_link=link,
            target_lecture_ids=target_lecture_ids,
        )
    }
    if valid_ids != set(normalized_ids):
        raise ValidationError(
            {
                "planned_clinic_link_ids": (
                    "현재 학생·테넌트·클리닉 세션에 유효한 미해결 항목만 저장할 수 있습니다."
                )
            }
        )

    current_items = list(
        SessionParticipantPlanItem.objects.select_for_update()
        .filter(participant=participant, removed_at__isnull=True)
        .order_by("clinic_link_id", "id")
    )
    current_by_link_id = {int(item.clinic_link_id): item for item in current_items}
    removed_ids = sorted(set(current_by_link_id) - valid_ids)
    if removed_ids:
        SessionParticipantPlanItem.objects.filter(
            id__in=[current_by_link_id[link_id].id for link_id in removed_ids]
        ).update(
            removed_at=timezone.now(),
            removed_by=actor,
            removal_reason="staff_replace",
        )

    SessionParticipantPlanItem.objects.bulk_create(
        [
            SessionParticipantPlanItem(
                participant=participant,
                clinic_link_id=link_id,
                selected_by=actor,
            )
            for link_id in normalized_ids
            if link_id not in current_by_link_id
        ]
    )
    if hasattr(participant, "_active_plan_items"):
        delattr(participant, "_active_plan_items")
    return participant


def _actor_label(actor) -> str:
    if not actor:
        return "시스템"
    full_name = ""
    if hasattr(actor, "get_full_name"):
        full_name = actor.get_full_name()
    return (
        full_name
        or getattr(actor, "name", "")
        or getattr(actor, "username", "")
        or "시스템"
    )


def _session_schedule_text(session) -> str:
    if not session:
        return ""
    date = str(session.date) if getattr(session, "date", None) else ""
    start_time = str(session.start_time)[:5] if getattr(session, "start_time", None) else ""
    location = getattr(session, "location", "") or ""
    return " ".join(part for part in (date, start_time, location) if part)


def build_session_change_notification_context(
    *,
    session,
    actor=None,
    old_session=None,
    domain_object_id: str | None = None,
) -> dict[str, Any]:
    location = getattr(session, "location", "") if session else ""
    date = str(session.date) if session and session.date else ""
    start_time = str(session.start_time)[:5] if session and getattr(session, "start_time", None) else ""
    old_schedule = (
        _session_schedule_text(old_session)
        if old_session is not None
        else "이전 안내된 클리닉 일정"
    )
    if not old_schedule:
        old_schedule = "-"

    context = {
        "클리닉명": getattr(session, "title", "") if session else "",
        "장소": f"[변경] {location}" if location else "[변경]",
        "날짜": date,
        "시간": start_time,
        "클리닉장소": location,
        "클리닉날짜": date,
        "클리닉시간": start_time,
        "클리닉기존일정": old_schedule,
        "클리닉변동사항": _session_schedule_text(session) or "일정 변경",
        "클리닉수정자": _actor_label(actor),
    }
    if domain_object_id:
        context["_domain_object_id"] = domain_object_id
    return context


def session_change_notice_student_ids(*, tenant, session) -> list[int]:
    ids = SessionParticipant.objects.filter(
        tenant=tenant,
        session=session,
        status__in=SESSION_CHANGE_NOTICE_STATUSES,
        student__deleted_at__isnull=True,
    ).order_by("student_id").values_list("student_id", flat=True)
    return list(dict.fromkeys(ids))


def _reservation_notification(participant: SessionParticipant) -> ClinicNotificationEvent | None:
    if participant.status not in (
        SessionParticipant.Status.BOOKED,
        SessionParticipant.Status.PENDING,
    ):
        return None
    session = participant.session
    return ClinicNotificationEvent(
        trigger="clinic_reservation_created",
        student=participant.student,
        context={
            "클리닉명": getattr(session, "title", "") if session else "",
            "장소": getattr(session, "location", "") if session else "",
            "날짜": str(session.date) if session and session.date else "",
            "시간": str(session.start_time)[:5] if session and getattr(session, "start_time", None) else "",
            "_domain_object_id": f"clinic_participant_{participant.pk}",
        },
    )


def _booking_change_notification(
    *,
    new_booking: SessionParticipant,
    old_session,
    actor,
) -> ClinicNotificationEvent:
    new_session = new_booking.session
    return ClinicNotificationEvent(
        trigger="clinic_reservation_changed",
        student=new_booking.student,
        context=build_session_change_notification_context(
            session=new_session,
            old_session=old_session,
            actor=actor,
            domain_object_id=f"booking_change_{new_booking.pk}",
        ),
    )


def _status_notification(
    participant: SessionParticipant,
    next_status: str,
    *,
    actor=None,
) -> ClinicNotificationEvent | None:
    trigger_map = {
        SessionParticipant.Status.CANCELLED: "clinic_cancelled",
        SessionParticipant.Status.ATTENDED: "clinic_check_in",
        SessionParticipant.Status.NO_SHOW: "clinic_absent",
    }
    trigger = trigger_map.get(next_status)
    if not trigger:
        return None

    session = participant.session
    location = getattr(session, "location", "") if session else ""
    date = str(session.date) if session and session.date else ""
    start_time = (
        str(session.start_time)[:5]
        if session and getattr(session, "start_time", None)
        else ""
    )
    is_cancel = next_status == SessionParticipant.Status.CANCELLED
    is_absent = next_status == SessionParticipant.Status.NO_SHOW
    context = {
        "클리닉명": getattr(session, "title", "") if session else "",
        "장소": f"[취소] {location}" if is_cancel else f"[결석] {location}" if is_absent else location,
        "날짜": date,
        "시간": f"취소({start_time})" if is_cancel else f"결석({start_time})" if is_absent else start_time,
        "_domain_object_id": f"participant_{participant.pk}_{next_status}_{int(time.time())}",
    }
    if is_cancel:
        context.update(
            {
                "클리닉기존일정": _session_schedule_text(session) or "-",
                "클리닉변동사항": "예약 취소",
                "클리닉수정자": _actor_label(actor),
            }
        )
    if next_status in (SessionParticipant.Status.ATTENDED, SessionParticipant.Status.NO_SHOW):
        now_hm = timezone.now().strftime("%H:%M")
        context["도착시간"] = now_hm
        context["_actual_time"] = now_hm
    if next_status == SessionParticipant.Status.ATTENDED:
        context["등원구분"] = "지각" if participant.is_late else "정상"
    return ClinicNotificationEvent(
        trigger=trigger,
        student=participant.student,
        context=context,
    )


def _complete_notification(participant: SessionParticipant) -> ClinicNotificationEvent:
    session = participant.session
    now = timezone.now()
    return ClinicNotificationEvent(
        trigger="clinic_self_study_completed",
        student=participant.student,
        context={
            "클리닉명": getattr(session, "title", "") if session else "",
            "장소": getattr(session, "location", "") if session else "",
            "날짜": str(session.date) if session and session.date else now.strftime("%Y-%m-%d"),
            "시간": now.strftime("%H:%M"),
            "_domain_object_id": str(participant.pk),
        },
    )


def _checkout_notification(participant: SessionParticipant) -> ClinicNotificationEvent:
    session = participant.session
    checked_out_at = participant.checked_out_at or timezone.now()
    return ClinicNotificationEvent(
        trigger="clinic_check_out",
        student=participant.student,
        context={
            "클리닉명": getattr(session, "title", "") if session else "",
            "장소": getattr(session, "location", "") if session else "",
            "날짜": (
                str(session.date)
                if session and session.date
                else checked_out_at.strftime("%Y-%m-%d")
            ),
            "시간": checked_out_at.strftime("%H:%M"),
            "_actual_time": checked_out_at.strftime("%H:%M"),
            "_domain_object_id": f"participant_{participant.pk}_checkout",
        },
    )


@transaction.atomic
def change_participant_status(
    *,
    tenant,
    participant_id: int,
    next_status: str,
    actor,
    request_student=None,
    memo=None,
    is_late: bool = False,
) -> ParticipantTransitionResult:
    allowed_statuses = {choice[0] for choice in SessionParticipant.Status.choices}
    if next_status not in allowed_statuses:
        raise ValidationError({"detail": f"Invalid status: {next_status}"})

    participant = _locked_participant(tenant=tenant, participant_id=participant_id)
    if participant.checked_out_at is not None:
        raise ValidationError({"detail": "하원 처리된 참가자의 등원 상태는 변경할 수 없습니다."})
    transitions = STUDENT_STATUS_TRANSITIONS if request_student else STAFF_STATUS_TRANSITIONS
    valid_next = transitions.get(participant.status, set())
    if next_status not in valid_next:
        raise ValidationError(
            {"detail": f"'{participant.status}'에서 '{next_status}'(으)로 변경할 수 없습니다."}
        )

    if request_student:
        if participant.student_id != request_student.id:
            raise PermissionDenied("다른 학생의 예약을 수정할 수 없습니다.")
        if participant.status != SessionParticipant.Status.PENDING:
            raise PermissionDenied("승인 대기 중인 예약만 취소할 수 있습니다.")
        if next_status != SessionParticipant.Status.CANCELLED:
            raise PermissionDenied("학생은 예약 취소만 가능합니다.")

    changed_at = timezone.now()
    participant.status = next_status
    participant.status_changed_at = changed_at
    participant.status_changed_by = actor
    # Attendance corrections and study completion are separate facts.
    # Never erase the teacher-confirmed self-study result while correcting arrival.
    if next_status == SessionParticipant.Status.ATTENDED:
        participant.checked_in_at = changed_at
        participant.is_late = bool(is_late)
    else:
        participant.checked_in_at = None
        participant.is_late = False
    if memo is not None:
        participant.memo = memo
    update_fields = [
        "status",
        "memo",
        "status_changed_at",
        "status_changed_by",
        "checked_in_at",
        "is_late",
        "updated_at",
    ]
    participant.save(update_fields=update_fields)
    if next_status in (
        SessionParticipant.Status.ATTENDED,
        SessionParticipant.Status.NO_SHOW,
        SessionParticipant.Status.CANCELLED,
        SessionParticipant.Status.REJECTED,
    ):
        cancel_pending_clinic_participant_reminders(
            tenant_id=tenant.id,
            participant_id=participant.id,
        )
    return ParticipantTransitionResult(
        participant=participant,
        notification=_status_notification(participant, next_status, actor=actor),
    )


@transaction.atomic
def complete_participant(*, tenant, participant_id: int, actor) -> ParticipantTransitionResult:
    participant = _locked_participant(tenant=tenant, participant_id=participant_id)
    if participant.completed_at:
        raise ValidationError({"detail": "이미 완료 처리된 참가자입니다."})
    if participant.status not in COMPLETE_ALLOWED_STATUSES:
        raise ValidationError(
            {"detail": f"'{participant.get_status_display()}' 상태의 참가자는 완료 처리할 수 없습니다."}
        )

    participant.completed_at = timezone.now()
    participant.completed_by = actor
    participant.save(
        update_fields=[
            "completed_at",
            "completed_by",
            "updated_at",
        ]
    )
    cancel_pending_clinic_participant_reminders(
        tenant_id=tenant.id,
        participant_id=participant.id,
    )
    return ParticipantTransitionResult(
        participant=participant,
        notification=_complete_notification(participant),
    )


@transaction.atomic
def checkout_participant(*, tenant, participant_id: int, actor) -> ParticipantTransitionResult:
    participant = _locked_participant(tenant=tenant, participant_id=participant_id)
    if participant.checked_out_at is not None:
        raise ValidationError({"detail": "이미 하원 처리된 참가자입니다."})
    if (
        participant.status != SessionParticipant.Status.ATTENDED
        or participant.checked_in_at is None
    ):
        raise ValidationError({"detail": "등원 처리된 참가자만 하원 처리할 수 있습니다."})

    participant.checked_out_at = timezone.now()
    participant.checked_out_by = actor
    participant.save(update_fields=["checked_out_at", "checked_out_by", "updated_at"])
    cancel_pending_clinic_participant_reminders(
        tenant_id=tenant.id,
        participant_id=participant.id,
    )
    return ParticipantTransitionResult(
        participant=participant,
        notification=_checkout_notification(participant),
    )


@transaction.atomic
def uncomplete_participant(*, tenant, participant_id: int) -> ParticipantTransitionResult:
    participant = _locked_participant(tenant=tenant, participant_id=participant_id)
    if not participant.completed_at:
        raise ValidationError({"detail": "완료 처리되지 않은 참가자입니다."})

    participant.completed_at = None
    participant.completed_by = None
    participant.save(update_fields=["completed_at", "completed_by", "updated_at"])
    return ParticipantTransitionResult(participant=participant)


def build_clinic_reminder_send_times(
    *,
    now: datetime.datetime,
    interval_minutes: int,
    repeat_until: datetime.datetime,
) -> list[datetime.datetime]:
    """Build bounded same-day repeat times for one manual reminder plan."""
    if timezone.is_naive(now) or timezone.is_naive(repeat_until):
        raise ValidationError({"detail": "재촉 시작/종료 시각에는 시간대 정보가 필요합니다."})
    try:
        interval_minutes = int(interval_minutes)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"detail": "반복 간격은 분 단위 숫자여야 합니다."}) from exc
    if interval_minutes < CLINIC_REMINDER_MIN_INTERVAL_MINUTES:
        raise ValidationError(
            {"detail": f"반복 간격은 최소 {CLINIC_REMINDER_MIN_INTERVAL_MINUTES}분입니다."}
        )

    local_tz = timezone.get_current_timezone()
    current = now.astimezone(local_tz)
    until = repeat_until.astimezone(local_tz)
    if until.date() != current.date():
        raise ValidationError({"detail": "반복 재촉은 시작한 날짜 안에서만 설정할 수 있습니다."})
    if until.timetz().replace(tzinfo=None) > CLINIC_REMINDER_LATEST_TIME:
        raise ValidationError({"detail": "반복 재촉 종료는 밤 10시를 넘길 수 없습니다."})
    if until <= current:
        raise ValidationError({"detail": "반복 재촉 종료 시각은 현재보다 늦어야 합니다."})

    result: list[datetime.datetime] = []
    next_send = current + datetime.timedelta(minutes=interval_minutes)
    while next_send <= until:
        result.append(next_send)
        if len(result) > CLINIC_REMINDER_MAX_OCCURRENCES:
            raise ValidationError(
                {"detail": f"반복 재촉은 즉시 발송을 제외하고 최대 {CLINIC_REMINDER_MAX_OCCURRENCES}회까지 설정할 수 있습니다."}
            )
        next_send += datetime.timedelta(minutes=interval_minutes)
    if not result:
        raise ValidationError({"detail": "종료 전 발송할 반복 재촉 시각이 없습니다."})
    return result


def _ensure_active_student_for_tenant(*, tenant, student) -> None:
    if not student:
        raise ValidationError({"detail": "student가 필요합니다."})
    if getattr(student, "tenant_id", None) != getattr(tenant, "id", None):
        raise PermissionDenied("해당 학생에 접근할 권한이 없습니다.")
    if getattr(student, "deleted_at", None):
        raise ValidationError({"detail": "삭제된 학생은 클리닉 예약 대상이 될 수 없습니다."})


def _validate_student_session_eligibility(*, tenant, student, session) -> None:
    if not session:
        return
    if getattr(session, "tenant_id", None) != getattr(tenant, "id", None):
        raise PermissionDenied("해당 세션에 접근할 권한이 없습니다.")
    if session.date < timezone.localdate():
        raise ValidationError({"detail": "지난 날짜의 클리닉은 예약할 수 없습니다."})
    if session.target_grade:
        if not student.grade or session.target_grade != student.grade:
            raise PermissionDenied("해당 클리닉은 다른 학년 대상입니다. 본인 학년의 클리닉만 신청할 수 있습니다.")
    if session.target_school_type and session.target_school_type.strip():
        if not student.school_type or session.target_school_type != student.school_type:
            raise PermissionDenied("해당 클리닉은 다른 학교 유형 대상입니다.")
    target_lecture_ids = set(session.target_lectures.values_list("id", flat=True))
    if target_lecture_ids:
        enrolled_lecture_ids = active_enrolled_lecture_ids_for_student(tenant, student)
        if not target_lecture_ids & enrolled_lecture_ids:
            raise PermissionDenied("해당 클리닉은 특정 강의 수강생 대상입니다.")


def _preferred_active_enrollment_id(
    *,
    tenant,
    student,
    session=None,
    preferred_enrollment_id: int | None = None,
) -> int | None:
    return preferred_active_enrollment_id_for_student_session(
        tenant,
        student,
        session,
        preferred_enrollment_id=preferred_enrollment_id,
    )


def _clinic_reason_for_enrollment(*, tenant, enrollment_id: int | None) -> str | None:
    return clinic_reason_for_unresolved_auto_links(tenant, enrollment_id)


def _assert_session_capacity(*, tenant, session) -> None:
    if not session or session.max_participants is None:
        return
    current_booked = SessionParticipant.objects.filter(
        tenant=tenant,
        session=session,
        status__in=[
            SessionParticipant.Status.BOOKED,
            SessionParticipant.Status.PENDING,
        ],
    ).count()
    if current_booked >= session.max_participants:
        raise Conflict("해당 클리닉은 정원이 마감되었습니다.")


def _assert_no_active_duplicate(
    *,
    tenant,
    student,
    session=None,
    requested_date=None,
    requested_start_time=None,
) -> None:
    active_statuses = [
        SessionParticipant.Status.PENDING,
        SessionParticipant.Status.BOOKED,
    ]
    if session:
        exists = SessionParticipant.objects.filter(
            tenant=tenant,
            session=session,
            student=student,
            status__in=active_statuses,
        ).exists()
        if exists:
            raise Conflict("이미 해당 세션에 예약된 학생입니다.")
    elif requested_date and requested_start_time:
        exists = SessionParticipant.objects.filter(
            tenant=tenant,
            session__isnull=True,
            requested_date=requested_date,
            requested_start_time=requested_start_time,
            student=student,
            status__in=active_statuses,
        ).exists()
        if exists:
            raise Conflict("이미 해당 시간에 예약 신청이 있습니다.")


@transaction.atomic
def create_participant(
    *,
    tenant,
    validated_data: dict[str, Any],
    request_student=None,
) -> ParticipantWriteResult:
    session = validated_data.get("session")
    requested_date = validated_data.get("requested_date")
    requested_start_time = validated_data.get("requested_start_time")
    student = validated_data.get("student")
    enrollment = validated_data.get("enrollment")
    enrollment_id = enrollment.id if enrollment else None
    source = validated_data.get("source") or SessionParticipant.Source.MANUAL
    requested_status = validated_data.get("status")
    memo = validated_data.get("memo") or ""

    if not session and not (requested_date and requested_start_time):
        raise ValidationError({"detail": "session 또는 (requested_date + requested_start_time) 중 하나는 필수입니다."})
    if session and (requested_date or requested_start_time):
        raise ValidationError({"detail": "session과 requested_date/requested_start_time을 동시에 사용할 수 없습니다."})
    if session and getattr(session, "tenant_id", None) != getattr(tenant, "id", None):
        raise PermissionDenied("해당 세션에 접근할 권한이 없습니다.")
    if session and session.date < timezone.localdate():
        raise ValidationError({"detail": "지난 날짜의 클리닉은 예약할 수 없습니다."})

    if request_student:
        if student and student.id != request_student.id:
            raise PermissionDenied("다른 학생의 예약을 신청할 수 없습니다.")
        student = request_student
        source = SessionParticipant.Source.STUDENT_REQUEST
        if not session:
            raise ValidationError({"detail": "등록 가능한 클리닉을 선택해주세요. 해당 날짜에 열린 클리닉만 신청할 수 있습니다."})
        _validate_student_session_eligibility(tenant=tenant, student=request_student, session=session)
        requested_status = SessionParticipant.Status.PENDING
        if getattr(tenant, "clinic_auto_approve_booking", False):
            requested_status = SessionParticipant.Status.BOOKED

    if not student and enrollment_id:
        enrollment = clinic_enrollment_for_tenant(tenant, enrollment_id)
        if not enrollment:
            raise ValidationError({"detail": "해당 수강 등록 정보를 찾을 수 없습니다."})
        student = enrollment.student
    elif student and enrollment_id:
        enrollment = clinic_enrollment_for_tenant(tenant, enrollment_id)
        if not enrollment:
            raise ValidationError({"detail": "해당 수강 등록 정보를 찾을 수 없습니다."})
        if enrollment.student_id != student.id:
            raise ValidationError({"detail": "enrollment_id와 student가 일치하지 않습니다."})

    _ensure_active_student_for_tenant(tenant=tenant, student=student)

    if session:
        session = (
            Session.objects
            .filter(tenant=tenant)
            .select_for_update()
            .get(pk=session.pk)
        )
        _assert_session_capacity(tenant=tenant, session=session)

    _assert_no_active_duplicate(
        tenant=tenant,
        student=student,
        session=session,
        requested_date=requested_date,
        requested_start_time=requested_start_time,
    )

    if source == SessionParticipant.Source.MANUAL:
        participant_role = "manual"
    elif source == SessionParticipant.Source.STUDENT_REQUEST:
        participant_role = "manual"
    else:
        participant_role = "target"
    participant_role = validated_data.get("participant_role") or participant_role

    if not enrollment_id and student:
        enrollment_id = _preferred_active_enrollment_id(
            tenant=tenant,
            student=student,
            session=session,
        )

    clinic_reason = validated_data.get("clinic_reason") or _clinic_reason_for_enrollment(
        tenant=tenant,
        enrollment_id=enrollment_id,
    )

    if not requested_status:
        if source in (SessionParticipant.Source.MANUAL, SessionParticipant.Source.AUTO):
            default_status = SessionParticipant.Status.BOOKED
        elif getattr(tenant, "clinic_auto_approve_booking", False):
            default_status = SessionParticipant.Status.BOOKED
        else:
            default_status = SessionParticipant.Status.PENDING
    else:
        default_status = requested_status

    try:
        participant = SessionParticipant.objects.create(
            tenant=tenant,
            session=session,
            requested_date=requested_date,
            requested_start_time=requested_start_time,
            student=student,
            source=source,
            status=default_status,
            enrollment_id=enrollment_id,
            participant_role=participant_role,
            clinic_reason=clinic_reason,
            memo=memo,
        )
    except IntegrityError as exc:
        raise Conflict("이미 해당 세션에 예약된 학생입니다.") from exc

    return ParticipantWriteResult(
        participant=participant,
        notification=_reservation_notification(participant),
    )


@transaction.atomic
def change_participant_booking(
    *,
    tenant,
    participant_id: int,
    new_session_id: int | str,
    request_student,
    actor,
    memo=None,
) -> ParticipantWriteResult:
    if not new_session_id:
        raise ValidationError({"detail": "new_session_id가 필요합니다."})
    try:
        new_session_id = int(new_session_id)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"detail": "new_session_id는 숫자여야 합니다."}) from exc
    try:
        old_booking = (
            SessionParticipant.objects
            .select_for_update()
            .get(pk=participant_id, tenant=tenant, student__deleted_at__isnull=True)
        )
    except SessionParticipant.DoesNotExist as exc:
        raise NotFound("예약을 찾을 수 없습니다.") from exc

    is_staff_change = request_student is None
    booking_student = old_booking.student if is_staff_change else request_student
    _ensure_active_student_for_tenant(tenant=tenant, student=booking_student)
    if not is_staff_change:
        if old_booking.student_id != request_student.id:
            raise PermissionDenied("다른 학생의 예약을 변경할 수 없습니다.")
        if old_booking.status != SessionParticipant.Status.PENDING:
            raise PermissionDenied("승인 대기 중인 예약만 변경할 수 있습니다.")
    elif old_booking.status not in (
        SessionParticipant.Status.PENDING,
        SessionParticipant.Status.BOOKED,
        SessionParticipant.Status.NO_SHOW,
    ):
        raise ValidationError({"detail": "대기·예약·결석 상태에서만 보충 일정을 만들 수 있습니다."})
    if old_booking.session_id == new_session_id:
        raise ValidationError({"detail": "같은 세션으로는 변경할 수 없습니다."})

    try:
        new_session = (
            Session.objects
            .filter(tenant=tenant)
            .select_for_update()
            .get(pk=new_session_id)
        )
    except Session.DoesNotExist as exc:
        raise NotFound("변경할 세션을 찾을 수 없습니다.") from exc

    _validate_student_session_eligibility(tenant=tenant, student=booking_student, session=new_session)
    _assert_session_capacity(tenant=tenant, session=new_session)
    _assert_no_active_duplicate(tenant=tenant, student=booking_student, session=new_session)

    new_status = SessionParticipant.Status.BOOKED if is_staff_change else SessionParticipant.Status.PENDING
    if not is_staff_change and getattr(tenant, "clinic_auto_approve_booking", False):
        new_status = SessionParticipant.Status.BOOKED

    enrollment_id = _preferred_active_enrollment_id(
        tenant=tenant,
        student=booking_student,
        session=new_session,
        preferred_enrollment_id=old_booking.enrollment_id,
    )

    try:
        new_booking = SessionParticipant.objects.create(
            tenant=tenant,
            session=new_session,
            student=booking_student,
            status=new_status,
            source=(
                SessionParticipant.Source.MANUAL
                if is_staff_change
                else SessionParticipant.Source.STUDENT_REQUEST
            ),
            enrollment_id=enrollment_id,
            participant_role="manual",
            memo=memo or "",
        )
    except IntegrityError as exc:
        raise Conflict("이미 해당 세션에 예약된 학생입니다.") from exc

    old_session = old_booking.session
    if old_booking.status != SessionParticipant.Status.NO_SHOW:
        old_booking.status = SessionParticipant.Status.CANCELLED
        old_booking.status_changed_at = timezone.now()
        old_booking.status_changed_by = actor
        old_booking.save(
            update_fields=["status", "status_changed_at", "status_changed_by", "updated_at"]
        )

    return ParticipantWriteResult(
        participant=new_booking,
        notification=_booking_change_notification(
            new_booking=new_booking,
            old_session=old_session,
            actor=actor,
        ),
    )
