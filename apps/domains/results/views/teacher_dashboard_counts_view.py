# PATH: apps/domains/results/views/teacher_dashboard_counts_view.py
"""
Teacher Dashboard Counts

GET /results/admin/teacher-dashboard-counts/

선생앱 Today 대시보드 "지금 처리할 일" 위젯용 추가 카운트.
- 영상 인코딩 실패
- 매치업 검수 대기 (status=done)
- 채점 미완료 attempt (최근 7일)
"""
from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.matchup.models import MatchupDocument
from apps.domains.results.models.exam_attempt import ExamAttempt
from apps.support.video.models import Video


PENDING_ATTEMPT_STATUSES = ["pending", "grading", "failed"]
SCORE_PENDING_WINDOW_DAYS = 7


class TeacherDashboardCountsView(APIView):
    """
    GET /api/v1/results/admin/teacher-dashboard-counts/

    Response:
    {
      "video_failed": int,
      "matchup_review_pending": int,
      "score_pending": int
    }
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response(
                {"video_failed": 0, "matchup_review_pending": 0, "score_pending": 0},
                status=200,
            )

        video_failed = Video.objects.filter(
            tenant=tenant,
            status=Video.Status.FAILED,
            deleted_at__isnull=True,
        ).count()

        matchup_review_pending = MatchupDocument.objects.filter(
            tenant=tenant,
            status="done",
        ).count()

        cutoff = timezone.now() - timedelta(days=SCORE_PENDING_WINDOW_DAYS)
        score_pending = ExamAttempt.objects.filter(
            exam__tenant=tenant,
            is_representative=True,
            status__in=PENDING_ATTEMPT_STATUSES,
            created_at__gte=cutoff,
        ).count()

        return Response(
            {
                "video_failed": int(video_failed),
                "matchup_review_pending": int(matchup_review_pending),
                "score_pending": int(score_pending),
            },
            status=200,
        )
