"""Cross-domain helpers used by clinic session adapters/services.

The clinic Django CRUD layer should not import other domain internals directly.
This support module is the compatibility boundary while the broader cutover is
in progress.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable


def empty_lecture_queryset():
    from apps.domains.lectures.models import Lecture

    return Lecture.objects.none()


def empty_enrollment_queryset():
    from apps.domains.enrollment.models import Enrollment

    return Enrollment.objects.none()


def lectures_for_tenant(tenant):
    from apps.domains.lectures.models import Lecture

    return Lecture.objects.filter(tenant=tenant)


def sections_for_tenant(tenant):
    from apps.domains.lectures.models import Section

    return Section.objects.filter(tenant=tenant)


def enrollments_for_clinic_tenant(tenant):
    from apps.domains.enrollment.selectors import enrollments_for_tenant

    return enrollments_for_tenant(tenant)


def clinic_enrollment_for_tenant(tenant, enrollment_id: int | None):
    if not enrollment_id:
        return None

    from apps.domains.enrollment.selectors import enrollments_for_tenant

    return enrollments_for_tenant(tenant).filter(id=enrollment_id).first()


def active_enrolled_lecture_ids_for_student(tenant, student) -> set[int]:
    from apps.domains.enrollment.selectors import enrollments_for_tenant

    return set(
        enrollments_for_tenant(tenant)
        .filter(student=student, status="ACTIVE")
        .values_list("lecture_id", flat=True)
    )


def latest_active_enrollment_id_for_student(tenant, student) -> int | None:
    from apps.domains.enrollment.selectors import enrollments_for_tenant

    enrollment = (
        enrollments_for_tenant(tenant)
        .filter(student=student, status="ACTIVE")
        .order_by("-enrolled_at", "-id")
        .first()
    )
    return enrollment.id if enrollment else None


def active_students_for_clinic_tenant(tenant):
    from apps.domains.students.selectors import students_for_tenant

    return students_for_tenant(tenant, deleted="active")


def get_student_for_clinic_request(request):
    from apps.domains.student_app.permissions import get_request_student

    return get_request_student(request)


def unresolved_clinic_enrollment_ids(tenant, enrollment_ids: Iterable[int]) -> set[int]:
    from apps.domains.progress.models import ClinicLink

    ids = [int(enrollment_id) for enrollment_id in enrollment_ids if enrollment_id]
    if not ids:
        return set()
    return set(
        ClinicLink.objects
        .filter(
            tenant=tenant,
            is_auto=True,
            resolved_at__isnull=True,
            enrollment_id__in=ids,
        )
        .values_list("enrollment_id", flat=True)
        .distinct()
    )


def clinic_reason_for_unresolved_auto_links(tenant, enrollment_id: int | None) -> str | None:
    if not enrollment_id:
        return None

    from apps.domains.progress.models import ClinicLink

    links = ClinicLink.objects.filter(
        tenant=tenant,
        enrollment_id=enrollment_id,
        is_auto=True,
        resolved_at__isnull=True,
    )
    has_exam = links.filter(source_type="exam").exists()
    has_homework = links.filter(source_type="homework").exists()
    if has_exam and has_homework:
        return "both"
    if has_exam:
        return "exam"
    if has_homework:
        return "homework"
    return None


def storage_presigned_get_url(r2_key: str, *, expires_in: int = 3600) -> str:
    from django.conf import settings
    from libs.r2_client.presign import create_presigned_get_url

    return create_presigned_get_url(
        r2_key,
        expires_in=expires_in,
        bucket=settings.R2_STORAGE_BUCKET,
    )


def send_clinic_session_reminder(*, session_id: int):
    return send_clinic_reminder_for_students(session_id=session_id)


def send_clinic_reminder_for_students(*, session_id: int):
    """
    Send the clinic reminder Alimtalk for booked participants in one session.

    Clinic owns the session/participant selection. Messaging only owns the
    notification dispatch path.
    """
    from apps.domains.clinic.models import Session as ClinicSession, SessionParticipant
    from apps.domains.messaging.services.notification_service import send_event_notification

    session = (
        ClinicSession.objects
        .select_related("tenant")
        .filter(id=int(session_id))
        .first()
    )
    if not session:
        return {"status": "not_found", "message": "클리닉 세션을 찾을 수 없습니다."}

    participants = (
        SessionParticipant.objects
        .select_related("student")
        .filter(
            tenant_id=session.tenant_id,
            session_id=session.id,
            status=SessionParticipant.Status.BOOKED,
        )
    )

    context = {
        "클리닉명": (session.title or "클리닉").strip(),
        "장소": session.location or "",
        "날짜": session.date.isoformat() if session.date else "",
        "시간": session.start_time.strftime("%H:%M") if session.start_time else "",
        "_domain_object_id": f"clinic_session:{session.id}:reminder",
    }

    attempted = 0
    sent = 0
    for participant in participants:
        student = participant.student
        if not student:
            continue
        attempted += 1
        if send_event_notification(
            tenant=session.tenant,
            trigger="clinic_reminder",
            student=student,
            send_to="student",
            context=context,
        ):
            sent += 1

    return {
        "status": "ok",
        "attempted": attempted,
        "sent": sent,
        "skipped": max(0, attempted - sent),
    }


def send_due_clinic_reminders(
    *,
    now=None,
    tenant_id: int | None = None,
    window_minutes: int = 5,
    dry_run: bool = False,
) -> dict:
    """
    Send clinic reminders whose due time has arrived.

    due_time = clinic session start - AutoSendConfig.minutes_before.
    The default window catches small scheduler delays while avoiding old sessions.
    """
    from django.db.models import Count, Q
    from django.utils import timezone

    from apps.domains.clinic.models import Session as ClinicSession, SessionParticipant
    from apps.domains.messaging.models import AutoSendConfig

    current = timezone.localtime(now or timezone.now())
    try:
        window = timedelta(minutes=max(0, int(window_minutes)))
    except (TypeError, ValueError):
        window = timedelta(minutes=5)

    configs = (
        AutoSendConfig.objects
        .filter(
            trigger="clinic_reminder",
            enabled=True,
            minutes_before__isnull=False,
            tenant__is_active=True,
        )
        .select_related("tenant")
        .order_by("tenant_id")
    )
    if tenant_id is not None:
        configs = configs.filter(tenant_id=int(tenant_id))

    stats = {
        "status": "ok",
        "dry_run": bool(dry_run),
        "configs": 0,
        "sessions_checked": 0,
        "sessions_due": 0,
        "attempted": 0,
        "sent": 0,
        "skipped": 0,
    }

    tz = timezone.get_current_timezone()
    for config in configs:
        stats["configs"] += 1
        try:
            minutes_before = int(config.minutes_before)
        except (TypeError, ValueError):
            stats["skipped"] += 1
            continue
        if minutes_before < 0:
            stats["skipped"] += 1
            continue

        earliest_start = current + timedelta(minutes=minutes_before) - window
        latest_start = current + timedelta(minutes=minutes_before)
        sessions = (
            ClinicSession.objects
            .filter(
                tenant_id=config.tenant_id,
                date__gte=earliest_start.date(),
                date__lte=latest_start.date(),
            )
            .annotate(
                booked_count=Count(
                    "participants",
                    filter=Q(participants__status=SessionParticipant.Status.BOOKED),
                    distinct=True,
                )
            )
            .filter(booked_count__gt=0)
            .order_by("date", "start_time", "id")
        )

        for session in sessions:
            stats["sessions_checked"] += 1
            if not session.date or not session.start_time:
                stats["skipped"] += 1
                continue
            start_at = datetime.combine(session.date, session.start_time)
            if timezone.is_naive(start_at):
                start_at = timezone.make_aware(start_at, tz)
            start_at = timezone.localtime(start_at)
            due_at = start_at - timedelta(minutes=minutes_before)
            if not (current - window <= due_at <= current):
                continue
            if start_at < current:
                continue

            stats["sessions_due"] += 1
            if dry_run:
                stats["attempted"] += int(session.booked_count or 0)
                continue

            result = send_clinic_reminder_for_students(session_id=session.id)
            stats["attempted"] += int(result.get("attempted") or 0)
            stats["sent"] += int(result.get("sent") or 0)
            stats["skipped"] += int(result.get("skipped") or 0)

    return stats


def send_clinic_event_notification(*, tenant, trigger: str, student, send_to: str, context: dict):
    from apps.domains.messaging.services import send_event_notification

    return send_event_notification(
        tenant=tenant,
        trigger=trigger,
        student=student,
        send_to=send_to,
        context=context,
    )


def unresolve_legacy_booking_links_for_session_delete(*, tenant, session) -> None:
    from django.db.models import Q
    from apps.domains.clinic.models import SessionParticipant
    from apps.domains.progress.models import ClinicLink
    from apps.domains.progress.services.clinic_resolution_service import ClinicResolutionService

    enrollment_ids = list(
        SessionParticipant.objects.filter(
            tenant=tenant,
            session=session,
            enrollment_id__isnull=False,
            status__in=[
                SessionParticipant.Status.BOOKED,
                SessionParticipant.Status.PENDING,
            ],
        ).values_list("enrollment_id", flat=True)
    )
    if not enrollment_ids:
        return

    target_lecture_ids = list(session.target_lectures.values_list("id", flat=True))
    link_filter = Q(
        tenant=tenant,
        enrollment_id__in=enrollment_ids,
        is_auto=True,
        resolution_type="BOOKING_LEGACY",
        resolved_at__isnull=False,
        session__lecture__tenant=tenant,
    )
    if target_lecture_ids:
        link_filter &= Q(session__lecture_id__in=target_lecture_ids)

    for link in ClinicLink.objects.filter(link_filter):
        ClinicResolutionService.unresolve(clinic_link_id=link.id)
