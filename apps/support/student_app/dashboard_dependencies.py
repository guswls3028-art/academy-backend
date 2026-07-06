"""Cross-domain read helpers for the student dashboard."""

from __future__ import annotations

from typing import Any

from django.db.models import Q


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
            session_enrollments__enrollment__student=student,
            session_enrollments__enrollment__tenant=tenant,
            session_enrollments__enrollment__status="ACTIVE",
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
            status__in=[
                SessionParticipant.Status.PENDING,
                SessionParticipant.Status.BOOKED,
            ],
            session__isnull=False,
            session__date=today,
        )
        .select_related("session")
    )
