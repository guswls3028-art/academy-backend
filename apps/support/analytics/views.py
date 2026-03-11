# apps/support/analytics/views.py
from __future__ import annotations

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import TenantResolvedAndStaff
from apps.support.analytics.serializers import (
    ExamSummarySerializer,
    QuestionStatSerializer,
    WrongAnswerDistributionSerializer,
    ExamResultRowSerializer,
)
from apps.support.analytics.services.exam_analytics import (
    get_exam_summary,
    get_question_stats,
    get_top_wrong_questions,
    get_wrong_answer_distribution,
    get_exam_results,
)


def _get_tenant(request):
    tenant = getattr(request, "tenant", None)
    if not tenant:
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied("tenant required")
    return tenant


# ============================================================
# 시험 요약 통계
# ============================================================
class ExamAnalyticsSummaryView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request, exam_id: int):
        tenant = _get_tenant(request)
        data = get_exam_summary(exam_id=int(exam_id), tenant=tenant)
        return Response(ExamSummarySerializer(data).data)


# ============================================================
# 문항별 통계
# ============================================================
class ExamAnalyticsQuestionStatsView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request, exam_id: int):
        tenant = _get_tenant(request)
        data = get_question_stats(exam_id=int(exam_id), tenant=tenant)
        return Response(QuestionStatSerializer(data, many=True).data)


# ============================================================
# 오답 TOP
# ============================================================
class ExamAnalyticsTopWrongView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request, exam_id: int):
        tenant = _get_tenant(request)
        limit = int(request.query_params.get("limit") or 5)
        rows = get_top_wrong_questions(
            exam_id=int(exam_id),
            tenant=tenant,
            limit=limit,
        )
        return Response(QuestionStatSerializer(rows, many=True).data)


# ============================================================
# 오답 분포
# ============================================================
class ExamAnalyticsWrongDistributionView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request, exam_id: int, question_id: int):
        tenant = _get_tenant(request)
        limit = int(request.query_params.get("limit") or 5)
        data = get_wrong_answer_distribution(
            exam_id=int(exam_id),
            question_id=int(question_id),
            tenant=tenant,
            limit=limit,
        )
        return Response(WrongAnswerDistributionSerializer(data).data)


# ============================================================
# 관리자 성적 리스트 (신규)
# ============================================================
class ExamAnalyticsResultsView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request, exam_id: int):
        tenant = _get_tenant(request)
        rows = get_exam_results(exam_id=int(exam_id), tenant=tenant)
        return Response(ExamResultRowSerializer(rows, many=True).data)
