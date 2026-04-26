# PATH: apps/domains/results/views/teacher_dashboard_counts_view.py
"""
Teacher Dashboard Counts

GET /results/admin/teacher-dashboard-counts/

선생앱 Today 대시보드 "지금 처리할 일" 위젯용 추가 카운트.

설계 메모(2026-04-26):
- score_pending / matchup_review_pending 폐기. 사유:
  · score_pending(ExamAttempt) ↔ /teacher/submissions(Submission) 모델 불일치 + recent_submissions 위젯 중복.
  · matchup_review_pending: status="done"이 "사람 검수 대기" 의미 아님(AI 분석 완료). 액션 없음.
- video_failed만 유지 + 30일 윈도우. 30일 지난 실패 영상은 처리 가치 없음(영업 폐기).
"""
from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from apps.support.video.models import Video


VIDEO_FAILED_WINDOW_DAYS = 30


class TeacherDashboardCountsView(APIView):
    """
    GET /api/v1/results/admin/teacher-dashboard-counts/

    Response:
    {
      "video_failed": int
    }
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"video_failed": 0}, status=200)

        cutoff = timezone.now() - timedelta(days=VIDEO_FAILED_WINDOW_DAYS)
        video_failed = Video.objects.filter(
            tenant=tenant,
            status=Video.Status.FAILED,
            deleted_at__isnull=True,
            updated_at__gte=cutoff,
        ).count()

        return Response({"video_failed": int(video_failed)}, status=200)
