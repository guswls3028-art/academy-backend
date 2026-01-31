# PATH: apps/domains/exams/views/template_editor_view.py
from __future__ import annotations

from django.shortcuts import get_object_or_404

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.domains.exams.models import Exam
from apps.domains.exams.services.template_builder_service import TemplateBuilderService
from apps.domains.exams.serializers.template_editor import TemplateEditorSummarySerializer
from apps.domains.results.permissions import IsTeacherOrAdmin


class TemplateEditorView(APIView):
    """
    Template Editor 초기 진입 API

    GET /api/v1/exams/<exam_id>/template-editor/

    역할:
    - 템플릿 시험을 편집 화면에 로딩하기 위한 단일 엔드포인트
    - builder 보장 + 상태 요약 반환
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request, exam_id: int):
        exam = get_object_or_404(Exam, id=int(exam_id))

        # 🔥 편집 진입 시 항상 최소 구조 보장
        init = TemplateBuilderService.ensure_initialized(exam)

        is_locked = exam.derived_exams.exists()

        payload = {
            "exam_id": exam.id,
            "title": exam.title,
            "subject": exam.subject,
            "sheet_id": init["sheet_id"],
            "total_questions": init["total_questions"],
            "has_answer_key": True,
            "is_locked": bool(is_locked),
        }

        return Response(TemplateEditorSummarySerializer(payload).data)
