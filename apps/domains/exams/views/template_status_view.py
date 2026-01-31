# PATH: apps/domains/exams/views/template_status_view.py
from __future__ import annotations

from django.shortcuts import get_object_or_404

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.domains.exams.models import Exam, ExamQuestion
from apps.domains.exams.services.template_resolver import resolve_template_exam


class TemplateStatusView(APIView):
    """
    Template 상태 점검 API

    GET /api/v1/exams/<exam_id>/template-status/

    목적:
    - 이 템플릿으로 '실제 시험 생성 가능 여부' 판단
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, exam_id: int):
        exam = get_object_or_404(Exam, id=int(exam_id))
        template = resolve_template_exam(exam)

        has_sheet = hasattr(template, "sheet")
        question_count = (
            ExamQuestion.objects
            .filter(sheet__exam=template)
            .count()
        )

        ready = bool(has_sheet and question_count > 0)

        return Response(
            {
                "template_exam_id": template.id,
                "has_sheet": has_sheet,
                "question_count": question_count,
                "is_ready_for_regular_exam": ready,
            }
        )
