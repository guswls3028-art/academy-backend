# ==========================================================================================
# FILE: apps/domains/results/views/admin_exam_result_detail_view.py
# ==========================================================================================
"""
Admin Exam Result Detail View (단일 학생 결과 상세)

GET /results/admin/exams/<exam_id>/enrollments/<enrollment_id>/

==========================================================================================
✅ PHASE 3 확정 계약 (FRONTEND LOCK)
==========================================================================================
응답 보장 필드:
- passed                : Exam.pass_score 기준 시험 합불
- clinic_required       : ClinicLink 단일 진실 (자동 트리거만)
- items[].is_editable   : edit_state 기반
- edit_state            : LOCK 판단 메타
- allow_retake
- max_attempts
- can_retake

⚠️ 주의
- passed ≠ SessionProgress.exam_passed
- 이 API는 "시험 단위(Result) 진실"
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
        pass_score = float(getattr(exam, "pass_score", 0.0) or 0.0)

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
        # 2️⃣ passed (시험 단위 기준)
        # -------------------------------------------------
        passed = bool(float(result.total_score or 0.0) >= pass_score)

        # -------------------------------------------------
        # 3️⃣ 재시험 정책 (⚠️ 기존 기능 유지)
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
        # 4️⃣ clinic_required (단일 진실)
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
        # 5️⃣ edit_state (LOCK 규칙)
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
        # 6️⃣ Serializer + items[].is_editable
        # -------------------------------------------------
        data = StudentExamResultSerializer(result).data

        for item in data.get("items", []):
            item["is_editable"] = bool(
                edit_state["can_edit"] and not edit_state["is_locked"]
            )

        # -------------------------------------------------
        # 7️⃣ 최종 응답 (기존 계약 + PHASE 3 확장)
        # -------------------------------------------------
        data.update({
            "passed": passed,
            "allow_retake": allow_retake,
            "max_attempts": max_attempts,
            "can_retake": can_retake,
            "clinic_required": bool(clinic_required),
            "edit_state": edit_state,
        })

        return Response(data)
