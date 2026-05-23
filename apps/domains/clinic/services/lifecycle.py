from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.domains.clinic.models import SessionParticipant


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

COMPLETE_ALLOWED_TRANSITIONS = {
    SessionParticipant.Status.PENDING,
    SessionParticipant.Status.BOOKED,
}


def _locked_participant(*, tenant, participant_id: int) -> SessionParticipant:
    try:
        return (
            SessionParticipant.objects
            .select_for_update()
            .select_related("student", "session")
            .get(pk=participant_id, tenant=tenant, student__deleted_at__isnull=True)
        )
    except SessionParticipant.DoesNotExist as exc:
        raise NotFound("예약을 찾을 수 없습니다.") from exc


def _status_notification(participant: SessionParticipant, next_status: str) -> ClinicNotificationEvent | None:
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
    if next_status in (SessionParticipant.Status.ATTENDED, SessionParticipant.Status.NO_SHOW):
        now_hm = timezone.now().strftime("%H:%M")
        context["도착시간"] = now_hm
        context["_actual_time"] = now_hm
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


@transaction.atomic
def change_participant_status(
    *,
    tenant,
    participant_id: int,
    next_status: str,
    actor,
    request_student=None,
    memo=None,
) -> ParticipantTransitionResult:
    allowed_statuses = {choice[0] for choice in SessionParticipant.Status.choices}
    if next_status not in allowed_statuses:
        raise ValidationError({"detail": f"Invalid status: {next_status}"})

    participant = _locked_participant(tenant=tenant, participant_id=participant_id)
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

    participant.status = next_status
    participant.status_changed_at = timezone.now()
    participant.status_changed_by = actor
    if memo is not None:
        participant.memo = memo
    participant.save(
        update_fields=[
            "status",
            "memo",
            "status_changed_at",
            "status_changed_by",
            "updated_at",
        ]
    )
    return ParticipantTransitionResult(
        participant=participant,
        notification=_status_notification(participant, next_status),
    )


@transaction.atomic
def complete_participant(*, tenant, participant_id: int, actor) -> ParticipantTransitionResult:
    participant = _locked_participant(tenant=tenant, participant_id=participant_id)
    if participant.completed_at:
        raise ValidationError({"detail": "이미 완료 처리된 참가자입니다."})
    if participant.status in (
        SessionParticipant.Status.CANCELLED,
        SessionParticipant.Status.REJECTED,
    ):
        raise ValidationError(
            {"detail": f"'{participant.get_status_display()}' 상태의 참가자는 완료 처리할 수 없습니다."}
        )

    participant.completed_at = timezone.now()
    participant.completed_by = actor
    if participant.status in COMPLETE_ALLOWED_TRANSITIONS:
        participant.status = SessionParticipant.Status.ATTENDED
        participant.status_changed_at = timezone.now()
        participant.status_changed_by = actor
    participant.save(
        update_fields=[
            "completed_at",
            "completed_by",
            "status",
            "status_changed_at",
            "status_changed_by",
            "updated_at",
        ]
    )
    return ParticipantTransitionResult(
        participant=participant,
        notification=_complete_notification(participant),
    )


@transaction.atomic
def uncomplete_participant(*, tenant, participant_id: int) -> ParticipantTransitionResult:
    participant = _locked_participant(tenant=tenant, participant_id=participant_id)
    if not participant.completed_at:
        raise ValidationError({"detail": "완료 처리되지 않은 참가자입니다."})

    participant.completed_at = None
    participant.completed_by = None
    if participant.status == SessionParticipant.Status.ATTENDED:
        participant.status = SessionParticipant.Status.BOOKED
        update_fields = ["completed_at", "completed_by", "status", "updated_at"]
    else:
        update_fields = ["completed_at", "completed_by", "updated_at"]
    participant.save(update_fields=update_fields)
    return ParticipantTransitionResult(participant=participant)
