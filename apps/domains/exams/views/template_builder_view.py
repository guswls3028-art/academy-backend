# PATH: apps/domains/exams/views/template_builder_view.py
from __future__ import annotations

from django.shortcuts import get_object_or_404

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.domains.exams.models import Exam
from apps.domains.exams.services.template_builder_service import TemplateBuilderService
from apps.domains.results.permissions import IsTeacherOrAdmin


class TemplateBuilderView(APIView):
    """
    Template Builder API

    POST /api/v1/exams/<exam_id>/builder/

    역할:
    - 템플릿 시험을 '편집 가능한 최소 완성 상태'로 보강
    - 프론트에서 템플릿 편집 화면 진입 시 단일 호출
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def post(self, request, exam_id: int):
        exam = get_object_or_404(Exam, id=int(exam_id))

        result = TemplateBuilderService.ensure_initialized(exam)

        return Response(result, status=200)
