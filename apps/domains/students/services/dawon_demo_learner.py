"""Date-based records available for the dedicated public Dawon sample."""

from collections import defaultdict

from django.db.models import Count, Exists, F, OuterRef, Q
from django.utils import timezone


def _local_day(timestamp):
    return timezone.localtime(timestamp).date()


def _with_attachments(queryset, tenant, post_id):
    from apps.domains.community.models import PostAttachment

    return queryset.annotate(has_attachments=Exists(
        PostAttachment.objects.filter(tenant=tenant, post_id=OuterRef(post_id)),
    ))


def session_materials(*, tenant, session_ids):
    from apps.domains.community.models import PostMapping

    now = timezone.now()
    mappings = PostMapping.objects.filter(
        node__tenant=tenant, node__level="SESSION", node__session_id__in=session_ids,
        node__lecture__tenant=tenant, node__session__lecture_id=F("node__lecture_id"),
        post__tenant=tenant, post__post_type="materials", post__status="published",
        post__support_kind__isnull=True, post__meta__dawon_public_sample=True,
    ).filter(Q(post__published_at__isnull=True) | Q(post__published_at__lte=now))
    materials = defaultdict(list)
    for session_id, pk, title, content, attached in _with_attachments(
        mappings, tenant, "post_id",
    ).order_by("post__created_at", "post_id").values_list(
        "node__session_id", "post_id", "post__title", "post__content", "has_attachments",
    ):
        materials[session_id].append({"id": pk, "title": title, "detail": content, "has_attachments": attached})
    return materials


def learner_topics(*, tenant, student):
    from apps.domains.clinic.contracts import DAWON_CLINIC_LOCATION as LOCATION, DAWON_CLINIC_MEMO as MEMO
    from apps.domains.clinic.models import Session, SessionParticipant
    from apps.domains.community.models import PostEntity
    from apps.domains.results.models.student_reported_score import StudentReportedScore

    now = timezone.now()
    published = PostEntity.objects.filter(
        tenant=tenant, status="published", support_kind__isnull=True,
    ).filter(Q(published_at__isnull=True) | Q(published_at__lte=now))
    from apps.support.community.post_dependencies import visible_scope_node_ids_for_students

    visible_nodes = visible_scope_node_ids_for_students(tenant=tenant, student_ids=[student.pk])
    public_notices = published.filter(post_type="notice", meta__dawon_public_sample=True).filter(
        Q(mappings__isnull=True) | Q(mappings__node_id__in=visible_nodes),
    ).distinct()
    notices = [{
        "id": post.pk, "has_attachments": post.has_attachments,
        "date": _local_day(post.published_at or post.created_at),
        "title": post.title,
        "detail": post.content,
    } for post in _with_attachments(public_notices, tenant, "pk")
        .order_by("-created_at")[:60]]

    statuses = {
        "pending": "승인 대기", "booked": "예약 확정", "attended": "참석",
        "no_show": "미참석", "cancelled": "취소", "rejected": "거절",
    }
    clinics = Session.objects.filter(
        tenant=tenant, memo=MEMO, location=LOCATION,
    ).order_by("date", "start_time")
    booking_by_session = {
        booking.session_id: booking for booking in SessionParticipant.objects.filter(
            tenant=tenant, student=student, session__in=clinics,
        ).order_by("updated_at")
    }
    clinic_rows = []
    for clinic in clinics:
        booking = booking_by_session.get(clinic.pk)
        if clinic.date < timezone.localdate() and booking is None:
            continue
        time = (booking.booking_start_time if booking else None) or clinic.start_time
        clinic_rows.append({
            "date": clinic.date, "title": clinic.title,
            "detail": f"{time:%H:%M} · {clinic.location} · {statuses[booking.status] if booking else '예약 가능'}",
            "booked": booking is not None,
        })

    from apps.core.authentication import _dawon_counsel_examples

    counsels = [{
        "date": _local_day(post.created_at),
        "title": post.title,
        "detail": "답변 완료" if post.reply_total else "답변 대기",
    } for post in published.filter(post_type="counsel", author_role="parent", created_by=student,
                                    title__in=_dawon_counsel_examples)
        .annotate(reply_total=Count("replies")).order_by("-created_at")[:100]
        if post.content == _dawon_counsel_examples[post.title]]
    from apps.domains.students.models import DawonCounselAppointment
    counsels.extend({"date": day, "title": "학습 상담 예정", "detail": lecture + " · 원장 등록"}
        for day, lecture in DawonCounselAppointment.objects.filter(
            enrollment__tenant=tenant, enrollment__student=student, enrollment__lecture__tenant=tenant,
        ).order_by("-day").values_list("day", "enrollment__lecture__title"))
    school_reports = list({
        (_local_day(score.created_at), score.academic_year, score.semester, score.exam_round): {
            "date": _local_day(score.created_at),
            "title": f"{score.academic_year}년 {score.semester}학기 {score.get_exam_round_display()}",
            "detail": "학교 성적 제출 기록",
            "academic_year": score.academic_year, "semester": score.semester, "exam_round": score.exam_round,
        }
        for score in StudentReportedScore.objects.filter(
            tenant=tenant, student=student, direct_entry=True,
            status=StudentReportedScore.Status.SELF_ENTERED,
        ).order_by("-created_at")[:100]
    }.values())
    return {
        "notices": notices, "clinics": clinic_rows, "counsels": counsels,
        "school_reports": school_reports,
    }
