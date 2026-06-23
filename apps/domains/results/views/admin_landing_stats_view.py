# PATH: apps/domains/results/views/admin_landing_stats_view.py
"""
Admin Results Landing Stats

GET /results/admin/landing-stats/

성적 도메인 첫 화면(KPI 인박스) 전용 집계 엔드포인트.
- 테넌트 격리 절대.
- 단일 라운드트립으로 KPI 4개 + 인박스 상위 N건.
"""
from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from academy.adapters.db.django import repositories_enrollment as enrollment_repo
from academy.adapters.db.django import repositories_exams as exams_repo
from academy.adapters.db.django import repositories_homework as homework_repo
from academy.adapters.db.django import repositories_submissions as submissions_repo
from apps.core.permissions import TenantResolvedAndStaff


SUBMISSION_TARGET_EXAM = submissions_repo.target_type_exam()
SUBMISSION_TARGET_HOMEWORK = submissions_repo.target_type_homework()
SUBMISSION_STATUS_DONE = submissions_repo.status_done()
SUBMISSION_STATUS_FAILED = submissions_repo.status_failed()
PENDING_STATUSES = submissions_repo.pending_statuses()


def _resolve_target_titles(submissions):
    """exam_id → title, lecture/session 정보 일괄 매핑."""
    exam_ids = {int(s.target_id) for s in submissions if s.target_type == SUBMISSION_TARGET_EXAM}
    homework_ids = {
        int(s.target_id) for s in submissions if s.target_type == SUBMISSION_TARGET_HOMEWORK
    }

    return (
        exams_repo.exam_target_info_map(exam_ids),
        homework_repo.homework_target_info_map(homework_ids),
    )


def _enrollment_student_map(submissions):
    enrollment_ids = {int(s.enrollment_id) for s in submissions if s.enrollment_id}
    if not enrollment_ids:
        return {}

    return enrollment_repo.enrollment_student_map_by_ids(enrollment_ids)


def _serialize_submissions(submissions, exam_map, homework_map, enrollment_map):
    items = []
    for s in submissions:
        if s.target_type == SUBMISSION_TARGET_EXAM:
            t = exam_map.get(int(s.target_id), {})
        elif s.target_type == SUBMISSION_TARGET_HOMEWORK:
            t = homework_map.get(int(s.target_id), {})
        else:
            t = {}

        student_name = ""
        if s.enrollment_id:
            enr = enrollment_map.get(int(s.enrollment_id))
            student = getattr(enr, "student", None) if enr else None
            if student:
                student_name = getattr(student, "name", "") or ""

        items.append(
            {
                "id": int(s.id),
                "target_type": s.target_type,
                "target_id": int(s.target_id),
                "target_title": t.get("target_title", ""),
                "lecture_id": t.get("lecture_id"),
                "lecture_title": t.get("lecture_title", ""),
                "session_id": t.get("session_id"),
                "enrollment_id": int(s.enrollment_id) if s.enrollment_id else None,
                "student_name": student_name,
                "status": s.status,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
        )
    return items


class AdminResultsLandingStatsView(APIView):
    """
    GET /results/admin/landing-stats/

    Response:
    {
      "active_lectures": int,
      "active_exams": int,
      "pending_submissions": int,
      "done_last_7d": int,
      "failed_last_24h": int,
      "pending_top": [SubmissionSummary, ...],   # 최신 5건
      "recent_done_top": [SubmissionSummary, ...]
    }
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response(
                {
                    "active_lectures": 0,
                    "active_exams": 0,
                    "pending_submissions": 0,
                    "done_last_7d": 0,
                    "failed_last_24h": 0,
                    "pending_top": [],
                    "recent_done_top": [],
                },
                status=200,
            )

        now = timezone.now()
        cutoff_7d = now - timedelta(days=7)
        cutoff_24h = now - timedelta(hours=24)

        active_lectures = enrollment_repo.active_non_system_lecture_count(tenant)

        active_exams = exams_repo.active_regular_exam_count(tenant)

        sub_qs = submissions_repo.submission_filter_tenant(tenant)
        pending_count = sub_qs.filter(status__in=PENDING_STATUSES).count()
        done_7d = sub_qs.filter(status=SUBMISSION_STATUS_DONE, created_at__gte=cutoff_7d).count()
        failed_24h = sub_qs.filter(
            status=SUBMISSION_STATUS_FAILED, created_at__gte=cutoff_24h
        ).count()

        pending_subs = list(
            sub_qs.filter(status__in=PENDING_STATUSES).order_by("-created_at")[:5]
        )
        recent_done = list(
            sub_qs.filter(status=SUBMISSION_STATUS_DONE).order_by("-created_at")[:5]
        )

        all_subs = pending_subs + recent_done
        exam_map, homework_map = _resolve_target_titles(all_subs)
        enrollment_map = _enrollment_student_map(all_subs)

        return Response(
            {
                "active_lectures": int(active_lectures),
                "active_exams": int(active_exams),
                "pending_submissions": int(pending_count),
                "done_last_7d": int(done_7d),
                "failed_last_24h": int(failed_24h),
                "pending_top": _serialize_submissions(
                    pending_subs, exam_map, homework_map, enrollment_map
                ),
                "recent_done_top": _serialize_submissions(
                    recent_done, exam_map, homework_map, enrollment_map
                ),
            },
            status=200,
        )
