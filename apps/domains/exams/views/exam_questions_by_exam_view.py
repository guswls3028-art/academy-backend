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
    - regular exam인 경우 template_exam의 문항을 반환 (effective_template_exam_id 기반)
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, exam_id: int):
        from apps.domains.exams.models import Exam

        tenant = request.tenant

        # effective_template_exam_id resolve (regular → template)
        try:
            exam = Exam.objects.get(id=exam_id)
            resolved_exam_id = exam.effective_template_exam_id
        except Exam.DoesNotExist:
            resolved_exam_id = exam_id

        qs = (
            ExamQuestion.objects
            .filter(
                sheet__exam_id=resolved_exam_id,
            )
            .filter(
                Q(sheet__exam__sessions__lecture__tenant=tenant)
                | Q(sheet__exam__derived_exams__sessions__lecture__tenant=tenant)
                | Q(sheet__exam__tenant=tenant)
            )
            .select_related("sheet")
            .prefetch_related("explanation")
            .order_by("number")
            .distinct()
        )
        return Response(QuestionSerializer(qs, many=True).data)
