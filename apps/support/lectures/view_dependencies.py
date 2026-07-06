"""Cross-domain read helpers for lecture views."""

from __future__ import annotations

from typing import Any

from django.db.models import Count


def active_enrollment_total_by_lecture_ref(lecture_id_ref: Any):
    from apps.domains.enrollment.models import Enrollment

    return (
        Enrollment.objects
        .filter(lecture_id=lecture_id_ref, status="ACTIVE")
        .order_by()
        .values("lecture_id")
        .annotate(total=Count("id"))
        .values("total")[:1]
    )


def list_unassigned_active_enrollments(tenant: Any, lecture: Any, excluded_ids: set[int]):
    from apps.domains.enrollment.models import Enrollment

    return list(
        Enrollment.objects
        .filter(tenant=tenant, lecture=lecture, status="ACTIVE")
        .exclude(id__in=excluded_ids)
    )


def first_session_delete_blocker(sessions: Any) -> str | None:
    from apps.domains.attendance.models import Attendance
    from apps.domains.enrollment.models import SessionEnrollment
    from apps.domains.exams.models import Exam
    from apps.domains.homework.models import HomeworkAssignment, HomeworkEnrollment
    from apps.domains.homework_results.models import Homework, HomeworkScore
    from apps.domains.progress.models import (
        ClinicLink,
        LectureProgress,
        RiskLog,
        SessionProgress,
    )
    from apps.domains.results.models import ScoreEditDraft
    from apps.domains.video.models import Video, VideoFolder

    session_ids = sessions.values("id")
    checks = (
        ("session enrollments", SessionEnrollment.objects.filter(session_id__in=session_ids)),
        ("attendance records", Attendance.objects.filter(session_id__in=session_ids)),
        ("exams", Exam.objects.filter(sessions__in=sessions)),
        ("homework enrollments", HomeworkEnrollment.objects.filter(session_id__in=session_ids)),
        ("homework assignments", HomeworkAssignment.objects.filter(session_id__in=session_ids)),
        (
            "homeworks",
            Homework.objects
            .filter(session_id__in=session_ids)
            .exclude(meta__removed_from_session_at__isnull=False),
        ),
        ("homework scores", HomeworkScore.objects.filter(session_id__in=session_ids)),
        ("session progress", SessionProgress.objects.filter(session_id__in=session_ids)),
        ("lecture progress references", LectureProgress.objects.filter(last_session_id__in=session_ids)),
        ("clinic links", ClinicLink.objects.filter(session_id__in=session_ids)),
        ("risk logs", RiskLog.objects.filter(session_id__in=session_ids)),
        ("videos", Video.all_with_deleted.filter(session_id__in=session_ids)),
        ("video folders", VideoFolder.objects.filter(session_id__in=session_ids)),
        ("score edit drafts", ScoreEditDraft.objects.filter(session_id__in=session_ids)),
    )
    for label, qs in checks:
        if qs.exists():
            return label
    return None
