# apps/domains/results/views/student_exam_result_view.py
from __future__ import annotations

from django.shortcuts import get_object_or_404

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.results.models import Result, ExamAttempt
from apps.domains.results.serializers.student_exam_result import (
    StudentExamResultSerializer,
)
from apps.domains.results.permissions import IsStudent

from apps.domains.exams.models import Exam
from apps.domains.enrollment.models import Enrollment

# ✅ Progress 조회 (clinic_required 계산용)
from apps.domains.progress.models import SessionProgress
from apps.domains.lectures.models import Session


class MyExamResultView(APIView):
    """
    GET /results/me/exams/<exam_id>/

    ✅ 단일 최종 버전 (중복 제거 완료)

    포함 기능:
    - Result + ResultItem 조회
    - allow_retake / max_attempts / can_retake 계산
    - clinic_required 주입
    - Enrollment 탐색 방어 로직

    ⚠️ 중요:
    - 이 View 정의는 반드시 1개만 존재해야 함
    - 다른 파일에 동일 클래스가 있으면 즉시 삭제할 것
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
        # 2️⃣ Result 조회 (최신 스냅샷)
        # -------------------------------------------------
        result = (
            Result.objects
            .filter(
                target_type="exam",
                target_id=int(exam_id),
                enrollment_id=enrollment_id,
            )
            .prefetch_related("items")
            .first()
        )

        if not result:
            return Response({"detail": "result not found"}, status=404)

        # -------------------------------------------------
        # 3️⃣ 재시험 버튼 판단 (대표 attempt 의존 ❌)
        # -------------------------------------------------
        allow_retake = bool(getattr(exam, "allow_retake", False))
        max_attempts = int(getattr(exam, "max_attempts", 1) or 1)

        attempt_count = ExamAttempt.objects.filter(
            exam_id=int(exam_id),
            enrollment_id=enrollment_id,
        ).count()

        can_retake = bool(allow_retake and attempt_count < max_attempts)

        # -------------------------------------------------
        # 4️⃣ clinic_required 계산 (Progress 파이프라인 결과)
        # -------------------------------------------------
        clinic_required = False
        session = Session.objects.filter(exam__id=exam_id).first()

        if session:
            sp = SessionProgress.objects.filter(
                session=session,
                enrollment_id=enrollment_id,
            ).first()
            clinic_required = bool(sp and getattr(sp, "clinic_required", False))

        # -------------------------------------------------
        # 5️⃣ 응답 구성
        # -------------------------------------------------
        data = StudentExamResultSerializer(result).data
        data["allow_retake"] = allow_retake
        data["max_attempts"] = max_attempts
        data["can_retake"] = can_retake
        data["clinic_required"] = clinic_required

        return Response(data)
