# PATH: apps/domains/exams/views/template_validation_view.py
from __future__ import annotations

from django.shortcuts import get_object_or_404

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.domains.exams.models import Exam
from apps.domains.exams.services.template_resolver import resolve_template_exam
from apps.domains.exams.services.template_validation_service import TemplateValidationService


class TemplateValidationView(APIView):
    """
    Template Validation API

    GET /api/v1/exams/<exam_id>/template-validation/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, exam_id: int):
        exam = get_object_or_404(Exam, id=int(exam_id))
        template = resolve_template_exam(exam)

        result = TemplateValidationService.validate_for_regular(template)

        return Response(
            {
                "template_exam_id": template.id,
                **result,
            }
        )
