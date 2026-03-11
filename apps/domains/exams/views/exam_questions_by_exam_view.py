from __future__ import annotations

from django.db.models import Q

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
        tenant = request.tenant
        qs = (
            ExamQuestion.objects
            .filter(
                sheet__exam_id=exam_id,
            )
            .filter(
                Q(sheet__exam__sessions__lecture__tenant=tenant)
                | Q(sheet__exam__derived_exams__sessions__lecture__tenant=tenant)
            )
            .select_related("sheet")
            .order_by("number")
            .distinct()
        )
        return Response(QuestionSerializer(qs, many=True).data)
