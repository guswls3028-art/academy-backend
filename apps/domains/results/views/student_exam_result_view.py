# apps/domains/results/views/student_exam_result_view.py
from __future__ import annotations

from django.shortcuts import get_object_or_404

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.results.models import Result, ExamAttempt
from apps.domains.results.serializers.student_exam_result import StudentExamResultSerializer
from apps.domains.results.permissions import IsStudent

from apps.domains.exams.models import Exam
from apps.domains.enrollment.models import Enrollment

# ✅ 단일 진실 유틸
from apps.domains.results.utils.session_exam import get_primary_session_for_exam
from apps.domains.results.utils.clinic import is_clinic_required


class MyExamResultView(APIView):
    """
    GET /results/me/exams/<exam_id>/

    ✅ 포함:
    - Result + items
    - 재시험 정책(allow_retake/max_attempts/can_retake)
    - clinic_required (ClinicLink 기준 단일화)
    """

    permission_classes = [IsAuthenticated, IsStudent]

    def get(self, request, exam_id: int):
        user = request.user
        exam = get_object_or_404(Exam, id=int(exam_id))

        # -------------------------------------------------
        # 1️⃣ Enrollment 찾기 (프로젝트별 필드 차이 방어)
        # -------------------------------------------------
        enrollment_qs = Enrollment.objects.all()
        if hasattr(Enrollment, "user_id"):
            enrollment_qs = enrollment_qs.filter(user_id=user.id)
        elif hasattr(Enrollment, "student_id"):
            enrollment_qs = enrollment_qs.filter(student_id=user.id)
        else:
            enrollment_qs = enrollment_qs.filter(user=user)

        enrollment = enrollment_qs.first()
        if not enrollment:
            return Response({"detail": "enrollment not found"}, status=404)

        enrollment_id = int(enrollment.id)

        # -------------------------------------------------
        # 2️⃣ Result 조회 (스냅샷)
        # -------------------------------------------------
        result = (
            Result.objects
            .filter(target_type="exam", target_id=int(exam_id), enrollment_id=enrollment_id)
            .prefetch_related("items")
            .first()
        )
        if not result:
            return Response({"detail": "result not found"}, status=404)

        # -------------------------------------------------
        # 3️⃣ 재시험 정책 판단 (attempt 기반)
        # -------------------------------------------------
        allow_retake = bool(getattr(exam, "allow_retake", False))
        max_attempts = int(getattr(exam, "max_attempts", 1) or 1)

        attempt_count = ExamAttempt.objects.filter(
            exam_id=int(exam_id),
            enrollment_id=enrollment_id,
        ).count()

        can_retake = bool(allow_retake and attempt_count < max_attempts)

        # -------------------------------------------------
        # 4️⃣ clinic_required (단일 진실: ClinicLink)
        # -------------------------------------------------
        clinic_required = False
        session = get_primary_session_for_exam(int(exam_id))
        if session:
            clinic_required = is_clinic_required(
                session=session,
                enrollment_id=enrollment_id,
                include_manual=False,  # ✅ 정책 통일(자동만)
            )

        # -------------------------------------------------
        # 5️⃣ 응답 구성
        # -------------------------------------------------
        data = StudentExamResultSerializer(result).data
        data["allow_retake"] = allow_retake
        data["max_attempts"] = max_attempts
        data["can_retake"] = can_retake
        data["clinic_required"] = bool(clinic_required)

        return Response(data)
