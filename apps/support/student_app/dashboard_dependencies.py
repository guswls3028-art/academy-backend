"""Cross-domain read helpers for the student dashboard."""

from __future__ import annotations

from typing import Any

from django.db.models import F, Q

from apps.support.student_app.learning_todo_policy import ongoing_lecture_filter


def notice_posts_for_dashboard(*, tenant: Any, student: Any | None):
    from apps.domains.community.models import PostMapping, ScopeNode
    from apps.domains.community.selectors import get_notice_posts_for_tenant
    from apps.domains.enrollment.selectors import active_enrollments_for_student

    notice_qs = get_notice_posts_for_tenant(tenant)
    if not student:
        return notice_qs

    enrolled_lecture_ids = set(
        active_enrollments_for_student(
            tenant=tenant,
            student=student,
        ).values_list("lecture_id", flat=True)
    )
    visible_node_ids = set(
        ScopeNode.objects.filter(
            tenant=tenant,
            lecture_id__in=enrolled_lecture_ids,
        ).values_list("id", flat=True)
    )
    scoped_post_ids = set(
        PostMapping.objects.filter(
            node_id__in=visible_node_ids,
        ).values_list("post_id", flat=True)
    )
    return notice_qs.filter(
        Q(mappings__isnull=True) | Q(id__in=scoped_post_ids)
    ).distinct()


def today_lecture_sessions_for_dashboard(*, tenant: Any, student: Any, today):
    from apps.domains.lectures.models import Session as LectureSession

    return (
        LectureSession.objects.filter(
            ongoing_lecture_filter(today=today),
            Q(lecture__start_date__isnull=True) | Q(lecture__start_date__lte=today),
            session_enrollments__tenant=tenant,
            session_enrollments__enrollment__student=student,
            session_enrollments__enrollment__tenant=tenant,
            session_enrollments__enrollment__status="ACTIVE",
            session_enrollments__enrollment__lecture_id=F("lecture_id"),
            session_enrollments__enrollment__student__deleted_at__isnull=True,
            lecture__tenant=tenant,
            date=today,
        )
        .select_related("lecture")
        .distinct()
        .order_by("order", "id")
    )


def today_clinic_participants_for_dashboard(*, tenant: Any, student: Any, today):
    from apps.domains.clinic.models import SessionParticipant

    return (
        SessionParticipant.objects.filter(
            student=student,
            tenant=tenant,
            session__tenant=tenant,
            status__in=[
                SessionParticipant.Status.PENDING,
                SessionParticipant.Status.BOOKED,
            ],
            session__isnull=False,
            session__date=today,
        )
        .select_related("session")
    )


def upcoming_clinic_count_for_dashboard(*, tenant: Any, student: Any, now) -> int:
    from datetime import timedelta

    from django.utils import timezone

    from apps.domains.clinic.models import SessionParticipant
    from apps.domains.clinic.time_ranges import booking_window, session_window

    local_now = timezone.localtime(now).replace(tzinfo=None)
    horizon = local_now + timedelta(days=7)
    participants = SessionParticipant.objects.filter(
        tenant=tenant, student=student, session__tenant=tenant,
        status__in=[SessionParticipant.Status.PENDING, SessionParticipant.Status.BOOKED],
        session__date__range=(local_now.date() - timedelta(days=1), horizon.date()),
    ).select_related("session")
    count = 0
    for participant in participants:
        if participant.booking_start_time is not None and participant.booking_end_time is not None:
            start, end = booking_window(
                session=participant.session,
                start_time=participant.booking_start_time,
                end_time=participant.booking_end_time,
            )
        else:
            start, end = session_window(participant.session)
        if end > local_now and start <= horizon:
            count += 1
    return count
