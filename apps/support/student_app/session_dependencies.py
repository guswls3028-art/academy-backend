"""Cross-domain dependencies for student session views."""

from __future__ import annotations

from datetime import date, time
from typing import Any, Iterable

from django.db.models import BooleanField, Count, F, Q, Value
from django.utils import timezone
from apps.support.lectures.schedule_queries import student_schedule_annotations


def get_active_student_session_ids(*, student: Any, tenant: Any) -> list[int]:
    from apps.domains.enrollment.models import SessionEnrollment

    return list(
        SessionEnrollment.objects.filter(
            tenant=tenant,
            enrollment__student=student,
            enrollment__tenant=tenant,
            enrollment__status="ACTIVE",
            enrollment__lecture__is_active=True,
            session__lecture__is_active=True,
            session__lecture__tenant=tenant,
            enrollment__lecture_id=F("session__lecture_id"),
        )
        .values_list("session_id", flat=True)
        .distinct()
    )


def get_student_lecture_sessions(
    *,
    session_ids: Iterable[int],
    tenant: Any,
    hidden_before: date | None,
    hidden_session_ids: set[int],
    student: Any = None,
) -> list[Any]:
    from apps.domains.lectures.models import Session as LectureSession

    base = LectureSession.objects.all()
    if student is not None:
        base = base.annotate(**student_schedule_annotations(tenant=tenant, student=student))
    else:
        base = base.annotate(effective_date=F("date"), schedule_configured=Value(False, output_field=BooleanField()))
    sessions = (
        base.filter(
            id__in=list(session_ids),
            lecture__tenant=tenant,
            lecture__is_active=True,
        )
        .filter(
            Q(schedule_configured=True)
            | Q(effective_date__lt=timezone.localdate())
            | Q(effective_date__isnull=True)
            | (
                (Q(lecture__end_date__isnull=True) | Q(effective_date__lte=F("lecture__end_date")))
                & (Q(lecture__start_date__isnull=True) | Q(effective_date__gte=F("lecture__start_date")))
            )
        )
        .select_related("lecture")
        .order_by("effective_date", "order", "id")
    )
    if hidden_before is not None:
        sessions = sessions.exclude(effective_date__lte=hidden_before)
    if hidden_session_ids:
        sessions = sessions.exclude(id__in=hidden_session_ids)
    return list(sessions)


def _owned_clinic_participants(*, student: Any, tenant: Any):
    from apps.domains.clinic.models import SessionParticipant

    return SessionParticipant.objects.filter(
        Q(session__isnull=True) | Q(session__tenant=tenant),
        student=student,
        student__tenant=tenant,
        tenant=tenant,
    )


def get_student_clinic_participants(*, student: Any, tenant: Any) -> list[Any]:
    from apps.domains.clinic.models import SessionParticipant

    return list(
        _owned_clinic_participants(student=student, tenant=tenant)
        .filter(
            status__in=[
                SessionParticipant.Status.PENDING,
                SessionParticipant.Status.BOOKED,
                SessionParticipant.Status.ATTENDED,
            ],
            session__isnull=False,
        )
        .select_related("session")
    )


def student_clinic_schedule_entry(participant) -> tuple[date, time, str]:
    from apps.domains.clinic.contracts import participant_booking_window
    from apps.domains.clinic.models import SessionParticipant

    start, _ = participant_booking_window(participant)
    if participant.checked_out_at is not None:
        status = "하원"
    elif participant.completed_at is not None:
        status = "학습 완료"
    elif participant.checked_in_at is not None or participant.status == SessionParticipant.Status.ATTENDED:
        status = "지각 등원" if participant.is_late else "등원"
    elif participant.status == SessionParticipant.Status.PENDING:
        status = "대기 중"
    else:
        status = "예약됨"
    return start.date(), start.time(), status


def get_future_hidden_session_ids(
    *,
    session_ids: set[int],
    tenant: Any,
    cutoff: date,
    student: Any = None,
) -> list[int]:
    if not session_ids:
        return []

    from apps.domains.lectures.models import Session as LectureSession

    return [
        int(session_id)
        for session_id in (
            LectureSession.objects
            .filter(id__in=session_ids, lecture__tenant=tenant)
            .annotate(**(student_schedule_annotations(tenant=tenant, student=student) if student else {"effective_date": F("date")}))
            .exclude(effective_date__lte=cutoff)
            .values_list("id", flat=True)
        )
    ]


def get_future_hidden_clinic_participant_ids(
    *,
    participant_ids: set[int],
    student: Any,
    tenant: Any,
    cutoff: date,
) -> list[int]:
    if not participant_ids:
        return []

    return [
        int(participant.id)
        for participant in _owned_clinic_participants(student=student, tenant=tenant)
        .filter(id__in=participant_ids).select_related("session")
        if participant.session_id is None or student_clinic_schedule_entry(participant)[0] > cutoff
    ]


def student_owns_session(*, student: Any, tenant: Any, session_id: Any) -> bool:
    from apps.domains.enrollment.models import SessionEnrollment

    return SessionEnrollment.objects.filter(
        tenant=tenant,
        enrollment__student=student,
        enrollment__tenant=tenant,
        enrollment__status="ACTIVE",
        enrollment__lecture__is_active=True,
        session__lecture__tenant=tenant,
        session__lecture__is_active=True,
        session_id=session_id,
        enrollment__lecture_id=F("session__lecture_id"),
    ).exists()


def student_owns_clinic_participant(
    *,
    student: Any,
    tenant: Any,
    participant_id: int,
) -> bool:
    return _owned_clinic_participants(student=student, tenant=tenant).filter(
        id=participant_id,
    ).exists()


def get_student_attendance_payload(*, student: Any, tenant: Any) -> tuple[dict, list[dict]]:
    from apps.domains.attendance.models import Attendance

    attendances = Attendance.objects.filter(
        tenant=tenant,
        enrollment__student=student,
        enrollment__tenant=tenant,
        enrollment__lecture__tenant=tenant,
        enrollment__status="ACTIVE",
        enrollment__student__deleted_at__isnull=True,
        session__lecture__tenant=tenant,
        enrollment__lecture_id=F("session__lecture_id"),
    )
    aggregate = attendances.aggregate(
        total=Count("id"),
        present=Count("id", filter=Q(status__in=["PRESENT", "ONLINE", "SUPPLEMENT"])),
        absent=Count("id", filter=Q(status="ABSENT")),
        late=Count("id", filter=Q(status="LATE")),
        early=Count("id", filter=Q(status="EARLY_LEAVE")),
        runaway=Count("id", filter=Q(status="RUNAWAY")),
    )

    accessible_session_ids = set(get_active_student_session_ids(student=student, tenant=tenant))
    recent = []
    for attendance in (
        attendances
        .annotate(**student_schedule_annotations(tenant=tenant, student=student, session_ref="session_id", date_ref="session__date"))
        .select_related("session", "session__lecture")
        .order_by("-effective_date", "-id")[:20]
    ):
        session = attendance.session
        recent.append({
            "session_id": session.id,
            "lecture_title": (
                getattr(session.lecture, "title", "") if session.lecture_id else ""
            ),
            "session_title": session.title or session.display_label,
            "date": attendance.effective_date.isoformat() if attendance.effective_date else None,
            "status": attendance.status,
            "qr_checked_in_at": attendance.qr_checked_in_at,
            "can_view_session": session.id in accessible_session_ids,
        })

    return {
        "total": aggregate["total"] or 0,
        "present": aggregate["present"] or 0,
        "absent": aggregate["absent"] or 0,
        "late": aggregate["late"] or 0,
        "early_leave": aggregate["early"] or 0,
        "runaway": aggregate["runaway"] or 0,
    }, recent


def get_student_detail_session(*, student: Any, tenant: Any, session_id: Any) -> Any | None:
    if not student_owns_session(student=student, tenant=tenant, session_id=session_id):
        return None

    from apps.domains.lectures.models import Session as LectureSession

    return (
        LectureSession.objects
        .filter(id=session_id, lecture__tenant=tenant)
        .annotate(**student_schedule_annotations(tenant=tenant, student=student))
        .select_related("lecture")
        .first()
    )
