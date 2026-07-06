"""Cross-domain notification helpers for clinic resolution."""

from __future__ import annotations


def send_clinic_resolution_notification(
    *,
    enrollment_id: int,
    session_id: int,
    resolution_type: str,
) -> None:
    from apps.domains.enrollment.models import Enrollment
    from apps.domains.messaging.services import send_event_notification

    enrollment = (
        Enrollment.objects
        .select_related("student", "tenant", "lecture")
        .filter(id=enrollment_id)
        .first()
    )
    if not enrollment or not enrollment.student or not enrollment.tenant:
        return

    result_label = {
        "EXAM_PASS": "시험 통과",
        "HOMEWORK_PASS": "과제 통과",
        "MANUAL_OVERRIDE": "수동 해소",
        "WAIVED": "면제",
        "SOURCE_REMOVED": "원본 삭제",
    }.get(resolution_type, "해소")

    session_location = ""
    session_date = ""
    session_time = ""
    if session_id:
        from apps.domains.clinic.models import Session as ClinicSession

        clinic_session = ClinicSession.objects.filter(pk=session_id).first()
        if clinic_session:
            session_location = getattr(clinic_session, "location", "") or ""
            session_date = str(clinic_session.date) if clinic_session.date else ""
            session_time = (
                str(clinic_session.start_time)[:5]
                if getattr(clinic_session, "start_time", None)
                else ""
            )

    context = {
        "클리닉명": str(getattr(enrollment.lecture, "title", "") or ""),
        "클리닉합불": result_label,
        "장소": session_location,
        "날짜": session_date,
        "시간": session_time,
        "_domain_object_id": f"{enrollment_id}:{session_id}",
    }
    for send_to in ("parent", "student"):
        send_event_notification(
            tenant=enrollment.tenant,
            trigger="clinic_result_notification",
            student=enrollment.student,
            send_to=send_to,
            context=context,
        )
