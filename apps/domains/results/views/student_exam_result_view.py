# PATH: apps/domains/results/views/student_exam_result_view.py
from __future__ import annotations

from django.shortcuts import get_object_or_404

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.results.models import Result
from apps.domains.results.serializers.student_exam_result import StudentExamResultSerializer
from apps.domains.results.permissions import IsStudent

# ✅ 시험/수강정보를 통해 enrollment_id를 찾기 위해 import
from apps.domains.exams.models import Exam
from apps.domains.enrollment.models import Enrollment


class MyExamResultView(APIView):
    """
    ✅ 학생 본인 시험 성적 조회 (1개 API)

    GET /results/me/exams/<exam_id>/

    - request.user(학생) 기준으로 enrollment_id를 찾아서
    - results_result + results_result_item을 반환

    주의:
    - Enrollment/Exam 필드가 프로젝트마다 다를 수 있어 방어적으로 작성함
    """

    permission_classes = [IsAuthenticated, IsStudent]

    def get(self, request, exam_id: int):
        user = request.user
        exam = get_object_or_404(Exam, id=int(exam_id))

        # ------------------------------------------------------------
        # 1) enrollment 찾기
        # ------------------------------------------------------------
        # ✅ 가장 이상적인 매핑: Enrollment(user + lecture)
        # - Exam에 lecture_id(혹은 lecture)가 있는 프로젝트를 전제로 우선 시도
        lecture_id = getattr(exam, "lecture_id", None)
        if lecture_id is None and getattr(exam, "lecture", None) is not None:
            lecture_id = getattr(exam.lecture, "id", None)

        enrollment_qs = Enrollment.objects.all()

        # 프로젝트별 Enrollment의 user FK 필드명이 다를 수 있음 (보통 user)
        if hasattr(Enrollment, "user_id"):
            enrollment_qs = enrollment_qs.filter(user_id=user.id)
        elif hasattr(Enrollment, "student_id"):
            enrollment_qs = enrollment_qs.filter(student_id=user.id)
        else:
            # 정말 특이 케이스면 여기 수정
            enrollment_qs = enrollment_qs.filter(user=user)

        if lecture_id is not None:
            # Enrollment에 lecture_id 필드가 있는 경우 우선 필터
            if hasattr(Enrollment, "lecture_id"):
                enrollment_qs = enrollment_qs.filter(lecture_id=int(lecture_id))

        enrollment = enrollment_qs.first()
        if not enrollment:
            return Response(
                {"detail": "enrollment not found for this user (and lecture)."},
                status=404,
            )

        enrollment_id = int(getattr(enrollment, "id"))

        # ------------------------------------------------------------
        # 2) Result 조회 (Result = 최신 스냅샷)
        # ------------------------------------------------------------
        result = Result.objects.filter(
            target_type="exam",
            target_id=int(exam_id),
            enrollment_id=enrollment_id,
        ).prefetch_related("items").first()

        if not result:
            # ✅ 학생 관점 UX:
            # - 아직 제출/채점 전이면 404가 더 자연스럽거나,
            # - 200 + null 형태로 주기도 함 (팀 컨벤션에 맞춰 택1)
            return Response(
                {"detail": "result not found (not submitted or not graded yet)."},
                status=404,
            )

        return Response(StudentExamResultSerializer(result).data)
