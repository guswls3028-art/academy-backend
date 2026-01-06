# apps/domains/exams/views/exam_questions_by_exam_view.py
from __future__ import annotations

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.exams.models import ExamQuestion
from apps.domains.exams.serializers.question import QuestionSerializer


class ExamQuestionsByExamView(APIView):
    """
    GET /exams/<exam_id>/questions/

    ✅ 목적
    - exam 기준으로 모든 ExamQuestion을 한 번에 조회
    - ResultItem.question_id → ExamQuestion 매핑용
    - 오답노트 / 문항통계 / bbox 하이라이트에 필수

    설계 포인트
    - QuestionViewSet(/exams/questions/)는 "전체"라 비효율
    - exam_id 기준 필터링된 전용 API가 운영/프론트 모두에 안전
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, exam_id: int):
        qs = (
            ExamQuestion.objects
            .filter(sheet__exam_id=int(exam_id))
            .select_related("sheet")
            .order_by("sheet_id", "number")
        )

        return Response(QuestionSerializer(qs, many=True).data)
