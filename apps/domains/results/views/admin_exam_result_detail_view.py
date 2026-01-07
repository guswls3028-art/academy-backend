# PATH: apps/domains/results/views/admin_exam_result_detail_view.py
from __future__ import annotations

from django.shortcuts import get_object_or_404

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import NotFound

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.models import Result, ExamAttempt
from apps.domains.results.serializers.student_exam_result import (
    StudentExamResultSerializer,
)

from apps.domains.exams.models import Exam
from apps.domains.progress.models import SessionProgress
from apps.domains.lectures.models import Session


class AdminExamResultDetailView(APIView):
    """
    GET /results/admin/exams/<exam_id>/enrollments/<enrollment_id>/

    ✅ 목적:
    - 기존 /admin/exams/<exam_id>/results/ 는 "전체 리스트" 용도 유지
    - 단일 학생 상세(Result + ResultItem)는 별도 endpoint로 분리해서 계약 충돌 제거

    ✅ 응답:
    - StudentExamResultSerializer(Result) 기반
    - allow_retake/max_attempts/can_retake/clinic_required 를 관리자 화면에서도 같이 제공 (optional UX)
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request, exam_id: int, enrollment_id: int):
        exam_id = int(exam_id)
        enrollment_id = int(enrollment_id)

        exam = get_object_or_404(Exam, id=exam_id)

        result = (
            Result.objects
            .filter(
                target_type="exam",
                target_id=exam_id,
                enrollment_id=enrollment_id,
            )
            .prefetch_related("items")
            .first()
        )

        if not result:
          # ✅ 명시적으로 404 (관리자 UI에서 분기 처리 쉬움)
            raise NotFound("result not found")

        # -------------------------------------------------
        # ✅ 재시험 정책 계산 (MyExamResultView 로직 존중)
        # -------------------------------------------------
        allow_retake = bool(getattr(exam, "allow_retake", False))
        max_attempts = int(getattr(exam, "max_attempts", 1) or 1)

        attempt_count = ExamAttempt.objects.filter(
            exam_id=exam_id,
            enrollment_id=enrollment_id,
        ).count()

        can_retake = bool(allow_retake and attempt_count < max_attempts)

        # -------------------------------------------------
        # ✅ clinic_required (progress pipeline 결과)
        # -------------------------------------------------
        clinic_required = False
        session = Session.objects.filter(exam__id=exam_id).first()
        if session:
            sp = SessionProgress.objects.filter(
                session=session,
                enrollment_id=enrollment_id,
            ).first()
            clinic_required = bool(sp and getattr(sp, "clinic_required", False))

        data = StudentExamResultSerializer(result).data
        data["allow_retake"] = allow_retake
        data["max_attempts"] = max_attempts
        data["can_retake"] = can_retake
        data["clinic_required"] = clinic_required

        return Response(data)
