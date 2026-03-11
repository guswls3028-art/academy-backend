# PATH: apps/domains/exams/views/template_validation_view.py
from __future__ import annotations

from django.db.models import Q
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
        tenant = request.tenant
        exam = get_object_or_404(
            Exam.objects.filter(
                Q(sessions__lecture__tenant=tenant)
                | Q(derived_exams__sessions__lecture__tenant=tenant)
            ).distinct(),
            id=int(exam_id),
        )
        template = resolve_template_exam(exam)

        result = TemplateValidationService.validate_for_regular(template)

        return Response(
            {
                "template_exam_id": template.id,
                **result,
            }
        )
