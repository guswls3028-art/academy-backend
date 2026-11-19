"""Date-based records available for the dedicated public Dawon sample."""

from django.db.models import Count, Q
from django.utils import timezone


def _local_day(timestamp):
    return timezone.localtime(timestamp).date()


def learner_topics(*, tenant, student):
    from apps.domains.clinic.dawon_demo import LOCATION, MEMO
    from apps.domains.clinic.models import Session, SessionParticipant
    from apps.domains.community.models import PostEntity
    from apps.domains.results.models.student_reported_score import StudentReportedScore

    now = timezone.now()
    published = PostEntity.objects.filter(
        tenant=tenant, status="published", support_kind__isnull=True,
    ).filter(Q(published_at__isnull=True) | Q(published_at__lte=now))
    notices = [{
        "date": _local_day(post.published_at or post.created_at),
        "title": post.title,
        "detail": post.content,
    } for post in published.filter(post_type="notice", meta__dawon_public_sample=True)
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
    school_reports = list({
        (_local_day(score.created_at), score.academic_year, score.semester, score.exam_round): {
            "date": _local_day(score.created_at),
            "title": f"{score.academic_year}년 {score.semester}학기 {score.get_exam_round_display()}",
            "detail": "예시 학생 입력 기록",
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
