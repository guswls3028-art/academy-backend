from __future__ import annotations

from django.shortcuts import get_object_or_404

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied

from apps.domains.exams.models import Sheet, Exam
from apps.domains.exams.serializers.question import QuestionSerializer
from apps.domains.exams.serializers.question_auto import QuestionAutoCreateSerializer
from apps.domains.exams.services.question_factory import create_questions_from_boxes

from apps.domains.results.permissions import IsTeacherOrAdmin


class SheetAutoQuestionsView(APIView):
    """
    POST /exams/sheets/<sheet_id>/auto-questions/

    봉인:
    - Teacher/Admin만 가능
    - template exam에서만 가능
    - template이 regular에 의해 사용 중이면 불가(서비스에서 방어)
    """

    def get_permissions(self):
        return [IsAuthenticated(), IsTeacherOrAdmin()]

    def post(self, request, sheet_id: int):
        sheet = get_object_or_404(Sheet.objects.select_related("exam"), id=int(sheet_id))
        if sheet.exam.exam_type != Exam.ExamType.TEMPLATE:
            raise PermissionDenied("Auto-question is allowed only for template exams.")

        s = QuestionAutoCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)

        boxes = [tuple(b) for b in s.validated_data["boxes"]]
        questions = create_questions_from_boxes(sheet_id=int(sheet_id), boxes=boxes)

        return Response(QuestionSerializer(questions, many=True).data)
