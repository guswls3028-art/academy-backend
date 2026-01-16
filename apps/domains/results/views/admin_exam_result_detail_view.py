# ==========================================================================================
# FILE: apps/domains/results/views/admin_exam_result_detail_view.py
# ==========================================================================================
"""
Admin Exam Result Detail View (단일 학생 결과 상세)

GET /results/admin/exams/<exam_id>/enrollments/<enrollment_id>/

==========================================================================================
✅ Phase 4 추가: edit_state
==========================================================================================
- 편집 가능 여부 판단용 메타 정보
- 현재는 "조회 전용"
- 추후 실시간 락 / Redis / DB Lock으로 확장 가능

판단 기준 (현재 고정):
- 대표 attempt.status == "grading"
  → can_edit = false
  → is_locked = true
  → lock_reason = "GRADING"
"""

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

# ✅ 단일 진실 유틸
from apps.domains.results.utils.session_exam import get_primary_session_for_exam
from apps.domains.results.utils.clinic import is_clinic_required


class AdminExamResultDetailView(APIView):
    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request, exam_id: int, enrollment_id: int):
        exam_id = int(exam_id)
        enrollment_id = int(enrollment_id)

        exam = get_object_or_404(Exam, id=exam_id)

        # -------------------------------------------------
        # 1️⃣ Result (대표 스냅샷)
        # -------------------------------------------------
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
            raise NotFound("result not found")

        # -------------------------------------------------
        # 2️⃣ 재시험 정책
        # -------------------------------------------------
        allow_retake = bool(getattr(exam, "allow_retake", False))
        max_attempts = int(getattr(exam, "max_attempts", 1) or 1)

        attempt_qs = ExamAttempt.objects.filter(
            exam_id=exam_id,
            enrollment_id=enrollment_id,
        )

        attempt_count = attempt_qs.count()
        can_retake = bool(allow_retake and attempt_count < max_attempts)

        # -------------------------------------------------
        # 3️⃣ clinic_required (단일 진실)
        # -------------------------------------------------
        clinic_required = False
        session = get_primary_session_for_exam(exam_id)
        if session:
            clinic_required = is_clinic_required(
                session=session,
                enrollment_id=enrollment_id,
                include_manual=False,
            )

        # -------------------------------------------------
        # 4️⃣ edit_state (Phase 4)
        # -------------------------------------------------
        edit_state = {
            "can_edit": True,
            "is_locked": False,
            "lock_reason": None,
            "last_updated_by": None,
            "updated_at": None,
        }

        if result.attempt_id:
            attempt = ExamAttempt.objects.filter(id=int(result.attempt_id)).first()
            if attempt and attempt.status == "grading":
                edit_state.update({
                    "can_edit": False,
                    "is_locked": True,
                    "lock_reason": "GRADING",
                })

        # -------------------------------------------------
        # 5️⃣ 응답 구성
        # -------------------------------------------------
        data = StudentExamResultSerializer(result).data
        data["allow_retake"] = allow_retake
        data["max_attempts"] = max_attempts
        data["can_retake"] = can_retake
        data["clinic_required"] = bool(clinic_required)
        data["edit_state"] = edit_state

        return Response(data)
