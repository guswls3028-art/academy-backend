# PATH: apps/domains/exams/views/exam_enrollment_view.py

from __future__ import annotations

from typing import List, Set

from django.db import transaction
from django.shortcuts import get_object_or_404

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status

from apps.domains.exams.models import ExamEnrollment
from apps.domains.exams.models.exam import Exam
from apps.domains.enrollment.models import SessionEnrollment

from apps.domains.exams.serializers.exam_enrollment_serializer import (
    ExamEnrollmentRowSerializer,
    ExamEnrollmentUpdateSerializer,
)


class ExamEnrollmentManageView(APIView):
    """
    Exam Enrollment Manage API

    ✅ 요구사항:
    - 세션(SessionEnrollment) 학생 중에서 선택하여
      시험(Exam)에 응시 대상자로 등록/제거 가능

    Endpoint:
    - GET /api/v1/exams/{exam_id}/enrollments/
    - PUT /api/v1/exams/{exam_id}/enrollments/

    규칙:
    - 시험은 최소 1개의 session과 연결되어 있어야 함 (Exam.sessions M2M)
    - enrollment_ids는 반드시 "해당 세션 등록 학생"의 enrollment_id만 허용
    """

    permission_classes = [IsAuthenticated]

    def _resolve_session_id(self, exam: Exam) -> int | None:
        """
        ✅ Exam.sessions(M2M) 기준으로 session_id를 찾는다.
        - 현재는 '첫 번째 세션'을 기준으로 동작.
        - (추후) 프론트에서 session_id를 query로 넘기면 더 정확해짐.
        """
        if hasattr(exam, "sessions"):
            s = exam.sessions.order_by("id").first()
            return int(s.id) if s else None

        # legacy fallback
        if hasattr(exam, "session_id") and exam.session_id:
            return int(exam.session_id)

        if hasattr(exam, "session") and exam.session:
            return int(exam.session.id)

        return None

    def get(self, request, exam_id: int):
        exam = get_object_or_404(Exam, pk=exam_id)

        session_id = self._resolve_session_id(exam)
        if not session_id:
            return Response(
                {"detail": "이 시험은 session_id가 없어 대상자 관리를 할 수 없습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 1) 세션 등록 학생 목록
        session_enrollments = (
            SessionEnrollment.objects
            .filter(session_id=session_id)
            .select_related("enrollment")
            .order_by("id")
        )

        # 2) 현재 시험에 선택된 enrollment_id set
        selected_ids: Set[int] = set(
            ExamEnrollment.objects.filter(exam_id=exam_id)
            .values_list("enrollment_id", flat=True)
        )

        # 3) UI row 구성
        items: List[dict] = []
        for se in session_enrollments:
            enrollment = getattr(se, "enrollment", None)
            student_name = ""

            if enrollment is not None:
                student = getattr(enrollment, "student", None)
                if student is not None:
                    student_name = str(getattr(student, "name", "") or "")
                else:
                    student_name = str(getattr(enrollment, "student_name", "") or "")

            items.append(
                {
                    "enrollment_id": int(se.enrollment_id),
                    "student_name": student_name,
                    "is_selected": int(se.enrollment_id) in selected_ids,
                }
            )

        return Response(
            {
                "exam_id": exam_id,
                "session_id": int(session_id),
                "items": ExamEnrollmentRowSerializer(items, many=True).data,
            }
        )

    @transaction.atomic
    def put(self, request, exam_id: int):
        exam = get_object_or_404(Exam, pk=exam_id)

        session_id = self._resolve_session_id(exam)
        if not session_id:
            return Response(
                {"detail": "이 시험은 session_id가 없어 대상자 관리를 할 수 없습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ser = ExamEnrollmentUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        incoming_ids = set(map(int, ser.validated_data["enrollment_ids"]))

        # ✅ 세션 등록 학생에 포함되는 enrollment_id만 허용
        valid_ids = set(
            SessionEnrollment.objects
            .filter(session_id=session_id)
            .values_list("enrollment_id", flat=True)
        )

        invalid = list(incoming_ids - valid_ids)
        if invalid:
            return Response(
                {
                    "detail": "세션 등록 학생이 아닌 enrollment_id가 포함되어 있습니다.",
                    "invalid_enrollment_ids": invalid,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ✅ 완전 치환 방식
        ExamEnrollment.objects.filter(exam_id=exam_id).delete()

        bulk = [
            ExamEnrollment(exam_id=exam_id, enrollment_id=eid)
            for eid in sorted(incoming_ids)
        ]
        if bulk:
            ExamEnrollment.objects.bulk_create(bulk, ignore_conflicts=True)

        return Response(
            {
                "exam_id": exam_id,
                "session_id": int(session_id),
                "selected_count": len(incoming_ids),
            },
            status=status.HTTP_200_OK,
        )
