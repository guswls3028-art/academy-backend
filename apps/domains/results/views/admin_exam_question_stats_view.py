# apps/domains/results/views/admin_exam_question_stats_view.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from django.db.models import Count, Q, F, FloatField, ExpressionWrapper

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.models import ResultFact


class AdminExamQuestionStatsView(APIView):
    """
    🔧 성능 패치
    - N+1 제거
    - 단일 aggregate 쿼리
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request, exam_id: int):
        facts = ResultFact.objects.filter(
            target_type="exam",
            target_id=int(exam_id),
        )

        rows = (
            facts.values("question_id")
            .annotate(
                attempts=Count("id"),
                correct_count=Count("id", filter=Q(is_correct=True)),
            )
            .annotate(
                wrong_count=F("attempts") - F("correct_count"),
                correct_rate=ExpressionWrapper(
                    F("correct_count") * 1.0 / F("attempts"),
                    output_field=FloatField(),
                ),
            )
            .order_by("question_id")
        )

        return Response(list(rows))
