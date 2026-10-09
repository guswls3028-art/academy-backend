# PATH: apps/domains/results/views/teacher_dashboard_counts_view.py
"""
Teacher Dashboard Counts

GET /results/admin/teacher-dashboard-counts/

선생앱 Today 대시보드 "지금 처리할 일" 위젯용 추가 카운트.

설계 메모(2026-04-26):
- score_pending / matchup_review_pending 폐기. 사유:
  · score_pending(ExamAttempt) ↔ /teacher/submissions(Submission) 모델 불일치 + recent_submissions 위젯 중복.
  · matchup_review_pending: status="done"이 "사람 검수 대기" 의미 아님(AI 분석 완료). 액션 없음.
- video_failed는 30일 윈도우. 대기 업무는 목록 페이지 수와 무관하게 집계한다.
"""
from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from apps.support.results.dashboard_dependencies import failed_video_count_since, pending_work_counts


VIDEO_FAILED_WINDOW_DAYS = 30


class TeacherDashboardCountsSerializer(serializers.Serializer):
    video_failed = serializers.IntegerField(min_value=0)
    qna_pending = serializers.IntegerField(min_value=0)
    counsel_pending = serializers.IntegerField(min_value=0)
    submission_pending = serializers.IntegerField(min_value=0)


class TeacherDashboardCountsView(APIView):
    """
    GET /api/v1/results/admin/teacher-dashboard-counts/

    Response:
    {
      "video_failed": int, "qna_pending": int,
      "counsel_pending": int, "submission_pending": int
    }
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @extend_schema(responses=TeacherDashboardCountsSerializer)
    def get(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant required"}, status=status.HTTP_403_FORBIDDEN)

        cutoff = timezone.now() - timedelta(days=VIDEO_FAILED_WINDOW_DAYS)
        video_failed = failed_video_count_since(tenant=tenant, cutoff=cutoff)

        return Response({"video_failed": int(video_failed), **pending_work_counts(tenant=tenant)}, status=200)
