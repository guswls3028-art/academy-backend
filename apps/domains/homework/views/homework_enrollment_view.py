# PATH: apps/domains/homework/views/homework_enrollment_view.py
from __future__ import annotations

from typing import List, Set

from django.db import transaction
from django.shortcuts import get_object_or_404

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status

from apps.domains.enrollment.models import SessionEnrollment
from apps.domains.homework.models import HomeworkEnrollment
from apps.domains.homework.serializers.homework_enrollment_serializer import (
    HomeworkEnrollmentRowSerializer,
    HomeworkEnrollmentUpdateSerializer,
)


class HomeworkEnrollmentManageView(APIView):
    """
    Homework Enrollment Manage API

    ✅ 요구사항:
    - 세션(SessionEnrollment) 학생 중에서 선택하여
      과제(Homework)에 응시 대상자로 등록/제거 가능

    Endpoint:
    - GET /api/v1/homework/enrollments/?session_id=123
    - PUT /api/v1/homework/enrollments/?session_id=123

    규칙:
    - session_id는 반드시 필요
    - enrollment_ids는 반드시 "해당 세션 등록 학생"의 enrollment_id만 허용
    - 저장 방식: 완전 치환(replace)
    """

    permission_classes = [IsAuthenticated]

    def _get_session_id_or_400(self, request) -> int:
        raw = request.query_params.get("session_id") or request.data.get("session_id")
        if not raw:
            raise ValueError("session_id is required")

        try:
            session_id = int(raw)
        except (TypeError, ValueError):
            raise ValueError("session_id must be integer")

        return session_id

    def get(self, request):
        try:
            session_id = self._get_session_id_or_400(request)
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        # 1) 세션 등록 학생 목록
        session_enrollments = (
            SessionEnrollment.objects
            .filter(session_id=session_id)
            .select_related("enrollment")
            .order_by("id")
        )

        # 2) 현재 과제에 선택된 enrollment_id set
        selected_ids: Set[int] = set(
            HomeworkEnrollment.objects
            .filter(session_id=session_id)
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
                "session_id": int(session_id),
                "items": HomeworkEnrollmentRowSerializer(items, many=True).data,
            }
        )

    @transaction.atomic
    def put(self, request):
        try:
            session_id = self._get_session_id_or_400(request)
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        ser = HomeworkEnrollmentUpdateSerializer(data=request.data)
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
        HomeworkEnrollment.objects.filter(session_id=session_id).delete()

        bulk = [
            HomeworkEnrollment(session_id=session_id, enrollment_id=eid)
            for eid in sorted(incoming_ids)
        ]
        if bulk:
            HomeworkEnrollment.objects.bulk_create(bulk, ignore_conflicts=True)

        return Response(
            {
                "session_id": int(session_id),
                "selected_count": len(incoming_ids),
            },
            status=status.HTTP_200_OK,
        )
