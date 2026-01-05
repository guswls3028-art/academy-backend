# apps/support/analytics/views.py
from __future__ import annotations

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

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
    get_exam_results,   # ✅ 신규
)


# ============================================================
# 시험 요약 통계
# ============================================================
class ExamAnalyticsSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, exam_id: int):
        data = get_exam_summary(exam_id=int(exam_id))
        return Response(ExamSummarySerializer(data).data)


# ============================================================
# 문항별 통계
# ============================================================
class ExamAnalyticsQuestionStatsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, exam_id: int):
        rows = get_question_stats(exam_id=int(exam_id))
        return Response(QuestionStatSerializer(rows, many=True).data)


# ============================================================
# 오답 TOP
# ============================================================
class ExamAnalyticsTopWrongView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, exam_id: int):
        limit = int(request.query_params.get("limit") or 5)
        rows = get_top_wrong_questions(
            exam_id=int(exam_id),
            limit=limit,
        )
        return Response(QuestionStatSerializer(rows, many=True).data)


# ============================================================
# 오답 분포
# ============================================================
class ExamAnalyticsWrongDistributionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, exam_id: int, question_id: int):
        limit = int(request.query_params.get("limit") or 5)
        data = get_wrong_answer_distribution(
            exam_id=int(exam_id),
            question_id=int(question_id),
            limit=limit,
        )
        return Response(WrongAnswerDistributionSerializer(data).data)


# ============================================================
# 관리자 성적 리스트 (신규)
# ============================================================
class ExamAnalyticsResultsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, exam_id: int):
        rows = get_exam_results(exam_id=int(exam_id))
        return Response(ExamResultRowSerializer(rows, many=True).data)
