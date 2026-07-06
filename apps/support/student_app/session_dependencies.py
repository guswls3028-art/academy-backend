"""Cross-domain dependencies for student session views."""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from django.db.models import Count, Q


def get_active_student_session_ids(*, student: Any, tenant: Any) -> list[int]:
    from apps.domains.enrollment.models import SessionEnrollment

    return list(
        SessionEnrollment.objects.filter(
            enrollment__student=student,
            enrollment__tenant=tenant,
            enrollment__status="ACTIVE",
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
) -> list[Any]:
    from apps.domains.lectures.models import Session as LectureSession

    sessions = (
        LectureSession.objects.filter(id__in=list(session_ids), lecture__tenant=tenant)
        .select_related("lecture")
        .order_by("date", "order", "id")
    )
    if hidden_before is not None:
        sessions = sessions.exclude(date__lte=hidden_before)
    if hidden_session_ids:
        sessions = sessions.exclude(id__in=hidden_session_ids)
    return list(sessions)


def get_student_clinic_participants(*, student: Any, tenant: Any) -> list[Any]:
    from apps.domains.clinic.models import SessionParticipant

    return list(
        SessionParticipant.objects
        .filter(
            student=student,
            tenant=tenant,
            status__in=[
                SessionParticipant.Status.PENDING,
                SessionParticipant.Status.BOOKED,
            ],
            session__isnull=False,
        )
        .select_related("session")
    )


def get_future_hidden_session_ids(
    *,
    session_ids: set[int],
    tenant: Any,
    cutoff: date,
) -> list[int]:
    if not session_ids:
        return []

    from apps.domains.lectures.models import Session as LectureSession

    return [
        int(session_id)
        for session_id in (
            LectureSession.objects
            .filter(id__in=session_ids, lecture__tenant=tenant)
            .exclude(date__lte=cutoff)
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

    from apps.domains.clinic.models import SessionParticipant

    return [
        int(participant_id)
        for participant_id in (
            SessionParticipant.objects
            .filter(id__in=participant_ids, student=student, tenant=tenant)
            .exclude(session__date__lte=cutoff)
            .values_list("id", flat=True)
        )
    ]


def student_owns_session(*, student: Any, tenant: Any, session_id: Any) -> bool:
    from apps.domains.enrollment.models import SessionEnrollment

    return SessionEnrollment.objects.filter(
        enrollment__student=student,
        enrollment__tenant=tenant,
        enrollment__status="ACTIVE",
        session__lecture__tenant=tenant,
        session_id=session_id,
    ).exists()


def student_owns_clinic_participant(
    *,
    student: Any,
    tenant: Any,
    participant_id: int,
) -> bool:
    from apps.domains.clinic.models import SessionParticipant

    return SessionParticipant.objects.filter(
        tenant=tenant,
        student=student,
        id=participant_id,
    ).exists()


def get_student_attendance_payload(*, student: Any, tenant: Any) -> tuple[dict, list[dict]]:
    from apps.domains.attendance.models import Attendance

    attendances = Attendance.objects.filter(
        tenant=tenant,
        enrollment__student=student,
        enrollment__status="ACTIVE",
        enrollment__student__deleted_at__isnull=True,
    )
    aggregate = attendances.aggregate(
        total=Count("id"),
        present=Count("id", filter=Q(status__in=["PRESENT", "ONLINE", "SUPPLEMENT"])),
        absent=Count("id", filter=Q(status="ABSENT")),
        late=Count("id", filter=Q(status="LATE")),
        early=Count("id", filter=Q(status="EARLY_LEAVE")),
        runaway=Count("id", filter=Q(status="RUNAWAY")),
    )

    recent = []
    for attendance in (
        attendances
        .select_related("session", "session__lecture")
        .order_by("-session__date", "-id")[:20]
    ):
        session = attendance.session
        recent.append({
            "session_id": session.id,
            "lecture_title": (
                getattr(session.lecture, "title", "") if session.lecture_id else ""
            ),
            "session_title": session.title or session.display_label,
            "date": session.date.isoformat() if session.date else None,
            "status": attendance.status,
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
        .select_related("lecture")
        .first()
    )
