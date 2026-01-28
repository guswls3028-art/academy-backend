from __future__ import annotations

from typing import List, Set

from django.db import transaction
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status

from apps.domains.enrollment.models import SessionEnrollment
from apps.domains.homework.models import HomeworkAssignment
from apps.domains.homework_results.models import Homework
from apps.domains.homework.serializers.homework_assignment_serializer import (
    HomeworkAssignmentRowSerializer,
    HomeworkAssignmentUpdateSerializer,
)


class HomeworkAssignmentManageView(APIView):
    """
    Homework Assignment Manage API (과제별 대상자)

    GET /homework/assignments/?homework_id=1
    PUT /homework/assignments/?homework_id=1
    """

    permission_classes = [IsAuthenticated]

    def _get_homework(self, request) -> Homework:
        hid = request.query_params.get("homework_id")
        if not hid:
            raise ValueError("homework_id is required")
        return Homework.objects.select_related("session").get(id=int(hid))

    def get(self, request):
        try:
            homework = self._get_homework(request)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)

        session_id = homework.session_id

        session_enrollments = (
            SessionEnrollment.objects
            .filter(session_id=session_id)
            .select_related("enrollment")
            .order_by("id")
        )

        selected_ids: Set[int] = set(
            HomeworkAssignment.objects
            .filter(homework=homework)
            .values_list("enrollment_id", flat=True)
        )

        items: List[dict] = []
        for se in session_enrollments:
            enrollment = getattr(se, "enrollment", None)
            student_name = ""

            if enrollment and hasattr(enrollment, "student"):
                student_name = str(enrollment.student.name or "")

            items.append(
                {
                    "enrollment_id": se.enrollment_id,
                    "student_name": student_name,
                    "is_selected": se.enrollment_id in selected_ids,
                }
            )

        return Response(
            {
                "homework_id": homework.id,
                "session_id": session_id,
                "items": HomeworkAssignmentRowSerializer(items, many=True).data,
            }
        )

    @transaction.atomic
    def put(self, request):
        try:
            homework = self._get_homework(request)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)

        ser = HomeworkAssignmentUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        incoming_ids = set(map(int, ser.validated_data["enrollment_ids"]))

        valid_ids = set(
            SessionEnrollment.objects
            .filter(session_id=homework.session_id)
            .values_list("enrollment_id", flat=True)
        )

        invalid = list(incoming_ids - valid_ids)
        if invalid:
            return Response(
                {
                    "detail": "세션 등록 학생이 아닌 enrollment_id 포함",
                    "invalid_enrollment_ids": invalid,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        HomeworkAssignment.objects.filter(homework=homework).delete()

        HomeworkAssignment.objects.bulk_create(
            [
                HomeworkAssignment(
                    homework=homework,
                    session=homework.session,
                    enrollment_id=eid,
                )
                for eid in sorted(incoming_ids)
            ]
        )

        return Response(
            {
                "homework_id": homework.id,
                "selected_count": len(incoming_ids),
            },
            status=status.HTTP_200_OK,
        )
