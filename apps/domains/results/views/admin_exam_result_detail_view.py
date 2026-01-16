# apps/domains/results/views/admin_exam_result_detail_view.py
"""
Admin Exam Result Detail View (단일 학생 결과 상세)

GET /results/admin/exams/<exam_id>/enrollments/<enrollment_id>/

==========================================================
✅ 이 파일의 목표 (계약/정합성)
==========================================================
1) "학생 상세 결과" 응답 포맷은 StudentExamResultSerializer 계약을 그대로 재사용한다.
   - 프론트/기존 화면과의 호환성 유지
   - items(문항별) 포함

2) Session ↔ Exam 매핑은 반드시 results.utils.session_exam의 "단일 진실" 유틸을 사용한다.
   - 프로젝트 히스토리상 관계가 (M2M / FK / reverse) 섞일 수 있음
   - View에서 직접 ORM join을 쓰면 화면마다 결과가 달라지는 버그가 발생하기 쉬움

3) clinic_required 기준은 다른 코드들과 동일하게 "ClinicLink(is_auto=True)"만 포함한다.
   - 수동 클리닉까지 섞이면 AdminExamResultsView / Summary / SessionScoreSummary와 불일치
   - 따라서 include_manual=False (default 정책)로 통일

4) 재시험 정책(allow_retake/max_attempts/can_retake)은 Student View와 동일 로직을 사용한다.
   - admin 화면에서도 학생 기준 정책을 보여주려는 의도(운영/CS)일 때 유용
   - UX 정책 변경 시 이 블록만 수정하면 됨

==========================================================
⚠️ 주의
==========================================================
- 이 View는 "Admin/Teacher" 전용이다.
- enrollment_id는 "Enrollment PK"로 가정한다(현재 results 도메인 전반과 동일).
  프로젝트마다 Enrollment 구조가 다를 수 있으나, 이 도메인 계약에서는 enrollment_id를 식별자로 사용한다.
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

# ✅ 단일 진실 유틸 (Session/Clinic)
from apps.domains.results.utils.session_exam import get_primary_session_for_exam
from apps.domains.results.utils.clinic import is_clinic_required


class AdminExamResultDetailView(APIView):
    """
    단일 학생(Enrollment) 시험 결과 상세

    - Result(스냅샷) + ResultItem(문항별)
    - allow_retake/max_attempts/can_retake
    - clinic_required (ClinicLink 기준 단일화)

    응답 포맷:
    - StudentExamResultSerializer(Result).data에
      allow_retake/max_attempts/can_retake/clinic_required를 주입해서 반환
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request, exam_id: int, enrollment_id: int):
        exam_id = int(exam_id)
        enrollment_id = int(enrollment_id)

        # -------------------------------------------------
        # 0) Exam 존재 확인
        # -------------------------------------------------
        exam = get_object_or_404(Exam, id=exam_id)

        # -------------------------------------------------
        # 1) Result 조회 (스냅샷)
        #    - attempt 대표 교체가 있더라도 Result는 항상 "현재 대표"를 가리키는 snapshot
        #    - items는 문항별 최신 상태
        # -------------------------------------------------
        result = (
            Result.objects.filter(
                target_type="exam",
                target_id=exam_id,
                enrollment_id=enrollment_id,
            )
            .prefetch_related("items")
            .first()
        )
        if not result:
            # 일관된 404
            raise NotFound("result not found")

        # -------------------------------------------------
        # 2) 재시험 정책 판단 (Student View와 동일)
        # -------------------------------------------------
        allow_retake = bool(getattr(exam, "allow_retake", False))
        max_attempts = int(getattr(exam, "max_attempts", 1) or 1)

        attempt_count = ExamAttempt.objects.filter(
            exam_id=exam_id,
            enrollment_id=enrollment_id,
        ).count()

        # can_retake = (allow_retake True) and (현재 시도 수 < 제한)
        # - allow_retake False이면 항상 False
        can_retake = bool(allow_retake and attempt_count < max_attempts)

        # -------------------------------------------------
        # 3) clinic_required (단일 진실)
        #    - Session ↔ Exam 매핑은 반드시 get_primary_session_for_exam 사용
        #    - ClinicLink는 자동 트리거만 포함(include_manual=False)
        # -------------------------------------------------
        clinic_required = False
        session = get_primary_session_for_exam(exam_id)
        if session:
            clinic_required = is_clinic_required(
                session=session,
                enrollment_id=enrollment_id,
                include_manual=False,  # ✅ 정책 통일: 자동만
            )

        # -------------------------------------------------
        # 4) 응답 구성
        #    - StudentExamResultSerializer 계약 재사용
        #    - 응답 전용 필드를 data에 주입
        # -------------------------------------------------
        data = StudentExamResultSerializer(result).data
        data["allow_retake"] = allow_retake
        data["max_attempts"] = max_attempts
        data["can_retake"] = can_retake
        data["clinic_required"] = bool(clinic_required)

        return Response(data)
