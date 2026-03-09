from __future__ import annotations

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.domains.exams.models import ExamQuestion
from apps.domains.exams.serializers.question import QuestionSerializer


class ExamQuestionsByExamView(APIView):
    """
    시험 기준 문항 조회
    - 요청한 exam_id 시험의 문항을 그대로 반환 (템플릿은 선택이므로 별도 resolve 없음)
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, exam_id: int):
        qs = (
            ExamQuestion.objects
            .filter(sheet__exam_id=exam_id)
            .select_related("sheet")
            .order_by("number")
        )
        return Response(QuestionSerializer(qs, many=True).data)
