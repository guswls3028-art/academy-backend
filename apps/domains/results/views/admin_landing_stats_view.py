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

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.submissions.models import Submission
from apps.domains.exams.models import Exam
from apps.domains.lectures.models import Lecture


PENDING_STATUSES = [
    Submission.Status.SUBMITTED,
    Submission.Status.DISPATCHED,
    Submission.Status.EXTRACTING,
    Submission.Status.NEEDS_IDENTIFICATION,
    Submission.Status.ANSWERS_READY,
    Submission.Status.GRADING,
]


def _resolve_target_titles(submissions):
    """exam_id → title, lecture/session 정보 일괄 매핑."""
    exam_ids = {int(s.target_id) for s in submissions if s.target_type == Submission.TargetType.EXAM}
    homework_ids = {
        int(s.target_id) for s in submissions if s.target_type == Submission.TargetType.HOMEWORK
    }

    exam_map = {}
    if exam_ids:
        for ex in Exam.objects.filter(id__in=exam_ids).prefetch_related("sessions__lecture"):
            session = ex.sessions.first()
            exam_map[ex.id] = {
                "target_title": ex.title,
                "lecture_id": session.lecture_id if session else None,
                "lecture_title": session.lecture.title if session and session.lecture else "",
                "session_id": session.id if session else None,
            }

    homework_map = {}
    if homework_ids:
        try:
            from apps.domains.homework_results.models import Homework

            for hw in Homework.objects.filter(id__in=homework_ids).select_related("session__lecture"):
                session = hw.session
                homework_map[hw.id] = {
                    "target_title": hw.title,
                    "lecture_id": session.lecture_id if session else None,
                    "lecture_title": session.lecture.title if session and session.lecture else "",
                    "session_id": session.id if session else None,
                }
        except Exception:
            pass

    return exam_map, homework_map


def _enrollment_student_map(submissions):
    enrollment_ids = {int(s.enrollment_id) for s in submissions if s.enrollment_id}
    if not enrollment_ids:
        return {}

    from apps.domains.enrollment.models import Enrollment

    return {
        enr.id: enr
        for enr in Enrollment.objects.select_related("student").filter(id__in=enrollment_ids)
    }


def _serialize_submissions(submissions, exam_map, homework_map, enrollment_map):
    items = []
    for s in submissions:
        if s.target_type == Submission.TargetType.EXAM:
            t = exam_map.get(int(s.target_id), {})
        elif s.target_type == Submission.TargetType.HOMEWORK:
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

        active_lectures = Lecture.objects.filter(
            tenant=tenant, is_active=True, is_system=False
        ).count()

        active_exams = Exam.objects.filter(
            tenant=tenant, is_active=True, exam_type=Exam.ExamType.REGULAR
        ).count()

        sub_qs = Submission.objects.filter(tenant=tenant)
        pending_count = sub_qs.filter(status__in=PENDING_STATUSES).count()
        done_7d = sub_qs.filter(status=Submission.Status.DONE, created_at__gte=cutoff_7d).count()
        failed_24h = sub_qs.filter(
            status=Submission.Status.FAILED, created_at__gte=cutoff_24h
        ).count()

        pending_subs = list(
            sub_qs.filter(status__in=PENDING_STATUSES).order_by("-created_at")[:5]
        )
        recent_done = list(
            sub_qs.filter(status=Submission.Status.DONE).order_by("-created_at")[:5]
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
