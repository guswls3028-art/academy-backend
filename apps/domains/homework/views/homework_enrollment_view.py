# PATH: apps/domains/homework/views/homework_enrollment_view.py

from __future__ import annotations

from typing import List, Set

from django.db import transaction
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
    permission_classes = [IsAuthenticated]

    def _get_session_id_or_400(self, request) -> int:
        raw = request.query_params.get("session_id") or request.data.get("session_id")
        if not raw:
            raise ValueError("session_id is required")
        return int(raw)

    def get(self, request):
        try:
            session_id = self._get_session_id_or_400(request)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)

        tenant = getattr(request, "tenant", None)

        session_enrollments = (
            SessionEnrollment.objects
            .filter(
                tenant=tenant,
                session_id=session_id,
            )
            .select_related("enrollment", "enrollment__student")
            .order_by("id")
        )

        selected_ids: Set[int] = set(
            HomeworkEnrollment.objects
            .filter(
                tenant=tenant,
                session_id=session_id,
            )
            .values_list("enrollment_id", flat=True)
        )

        items: List[dict] = []
        for se in session_enrollments:
            enrollment = se.enrollment
            student_name = (
                enrollment.student.name
                if enrollment and hasattr(enrollment, "student")
                else ""
            )

            items.append(
                {
                    "enrollment_id": se.enrollment_id,
                    "student_name": student_name,
                    "is_selected": se.enrollment_id in selected_ids,
                }
            )

        return Response(
            {
                "session_id": session_id,
                "items": HomeworkEnrollmentRowSerializer(items, many=True).data,
            }
        )

    @transaction.atomic
    def put(self, request):
        try:
            session_id = self._get_session_id_or_400(request)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)

        tenant = getattr(request, "tenant", None)

        ser = HomeworkEnrollmentUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        incoming_ids = set(map(int, ser.validated_data["enrollment_ids"]))

        valid_ids = set(
            SessionEnrollment.objects
            .filter(
                tenant=tenant,
                session_id=session_id,
            )
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

        HomeworkEnrollment.objects.filter(
            tenant=tenant,
            session_id=session_id,
        ).delete()

        HomeworkEnrollment.objects.bulk_create(
            [
                HomeworkEnrollment(
                    tenant=tenant,
                    session_id=session_id,
                    enrollment_id=eid,
                )
                for eid in sorted(incoming_ids)
            ]
        )

        return Response(
            {
                "session_id": session_id,
                "selected_count": len(incoming_ids),
            },
            status=status.HTTP_200_OK,
        )
