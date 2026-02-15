# apps/domains/results/views/student_exam_result_view.py
from __future__ import annotations

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.results.permissions import IsStudent
from apps.domains.results.services.student_result_service import get_my_exam_result_data


class MyExamResultView(APIView):
    """
    GET /results/me/exams/<exam_id>/

    ✅ 포함:
    - Result + items (exam_id, is_pass 포함 — 프론트 계약)
    - 재시험 정책(allow_retake/max_attempts/can_retake)
    - clinic_required (ClinicLink 기준 단일화)
    """

    permission_classes = [IsAuthenticated, IsStudent]

    def get(self, request, exam_id: int):
        data = get_my_exam_result_data(request, int(exam_id))
        return Response(data)
