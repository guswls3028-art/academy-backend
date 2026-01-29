from __future__ import annotations

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.domains.exams.models import ExamQuestion, Exam
from apps.domains.exams.serializers.question import QuestionSerializer
from apps.domains.exams.services.template_resolver import resolve_template_exam


class ExamQuestionsByExamView(APIView):
    """
    시험 기준 문항 조회
    - template → self
    - regular → template_exam
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, exam_id: int):
        exam = Exam.objects.get(id=exam_id)
        template = resolve_template_exam(exam)

        qs = (
            ExamQuestion.objects
            .filter(sheet__exam=template)
            .select_related("sheet")
            .order_by("number")
        )

        return Response(QuestionSerializer(qs, many=True).data)
