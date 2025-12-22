# apps/domains/exams/views/question_auto_view.py
from __future__ import annotations

from django.shortcuts import get_object_or_404

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.exams.models import Sheet
from apps.domains.exams.serializers.question import QuestionSerializer
from apps.domains.exams.serializers.question_auto import QuestionAutoCreateSerializer
from apps.domains.exams.services.question_factory import create_questions_from_boxes


class SheetAutoQuestionsView(APIView):
    """
    POST /exams/sheets/<sheet_id>/auto-questions/
    {
      "boxes": [[x,y,w,h], ...]
    }

    반환: 생성/업데이트된 ExamQuestion 목록
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, sheet_id: int):
        # ✅ 없으면 404 (정상)
        get_object_or_404(Sheet, id=int(sheet_id))

        s = QuestionAutoCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)

        boxes = [tuple(b) for b in s.validated_data["boxes"]]
        questions = create_questions_from_boxes(sheet_id=int(sheet_id), boxes=boxes)

        return Response(QuestionSerializer(questions, many=True).data)
