# apps/domains/results/views/question_stats_views.py

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.services.question_stats_service import QuestionStatsService
from apps.domains.results.serializers.question_stats import (
    QuestionStatSerializer,
    TopWrongQuestionSerializer,
)


class AdminExamQuestionStatsView(APIView):
    """
    GET /api/v1/results/admin/exams/{exam_id}/questions/

    ✅ 단일 진실:
    - ResultFact 기반 (append-only)
    - 대표 attempt 교체/재시험 여부와 무관하게 항상 일관된 통계
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request, exam_id: int):
        data = QuestionStatsService.per_question_stats(
            exam_id=int(exam_id),
        )
        return Response(QuestionStatSerializer(data, many=True).data)


class ExamQuestionWrongDistributionView(APIView):
    """
    GET /api/v1/results/admin/exams/{exam_id}/questions/{question_id}/wrong-distribution/
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request, exam_id: int, question_id: int):
        dist = QuestionStatsService.wrong_choice_distribution(
            exam_id=int(exam_id),
            question_id=int(question_id),
        )
        return Response(
            {
                "question_id": int(question_id),
                "distribution": dist,
            }
        )


class ExamTopWrongQuestionsView(APIView):
    """
    GET /api/v1/results/admin/exams/{exam_id}/questions/top-wrong/?n=5
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request, exam_id: int):
        n = int(request.query_params.get("n", 5))
        data = QuestionStatsService.top_n_wrong_questions(
            exam_id=int(exam_id),
            n=n,
        )
        return Response(TopWrongQuestionSerializer(data, many=True).data)
