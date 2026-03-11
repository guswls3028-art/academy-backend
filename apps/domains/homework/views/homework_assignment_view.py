# PATH: apps/domains/homework/views/homework_assignment_view.py

from __future__ import annotations

from typing import List, Set

from django.db import transaction
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status

from apps.core.permissions import TenantResolvedAndStaff

from apps.domains.enrollment.models import SessionEnrollment
from apps.domains.homework.models import HomeworkAssignment
from apps.domains.homework_results.models import Homework
from apps.domains.homework.serializers.homework_assignment_serializer import (
    HomeworkAssignmentRowSerializer,
    HomeworkAssignmentUpdateSerializer,
)


class HomeworkAssignmentManageView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def _get_homework(self, request) -> Homework:
        hid = request.query_params.get("homework_id")
        if not hid:
            raise ValueError("homework_id is required")

        tenant = getattr(request, "tenant", None)

        return Homework.objects.select_related(
            "session",
            "session__lecture",
        ).get(
            id=int(hid),
            session__lecture__tenant=tenant,  # ✅ tenant 안전장치
        )

    def get(self, request):
        try:
            homework = self._get_homework(request)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)

        tenant = getattr(request, "tenant", None)
        session_id = homework.session_id

        session_enrollments = (
            SessionEnrollment.objects
            .filter(
                tenant=tenant,
                session_id=session_id,
            )
            .filter(enrollment__student__deleted_at__isnull=True)
            .select_related("enrollment", "enrollment__student", "enrollment__lecture")
            .order_by("id")
        )

        selected_ids: Set[int] = set(
            HomeworkAssignment.objects
            .filter(
                tenant=tenant,
                homework=homework,
            )
            .values_list("enrollment_id", flat=True)
        )

        items: List[dict] = []
        request_obj = request
        for se in session_enrollments:
            enrollment = getattr(se, "enrollment", None)
            student_name = ""
            profile_photo_url = None
            lecture_title = ""
            lecture_color = ""
            lecture_chip_label = ""

            if enrollment is not None:
                student = getattr(enrollment, "student", None)
                if student is not None:
                    student_name = str(getattr(student, "name", "") or "")
                    if getattr(student, "profile_photo", None):
                        try:
                            profile_photo_url = request_obj.build_absolute_uri(
                                student.profile_photo.url
                            )
                        except Exception:
                            profile_photo_url = None
                else:
                    student_name = ""

                lecture = getattr(enrollment, "lecture", None)
                if lecture is not None:
                    lecture_title = str(getattr(lecture, "title", "") or "")
                    lecture_color = str(getattr(lecture, "color", "") or "") or "#3b82f6"
                    lecture_chip_label = str(getattr(lecture, "chip_label", "") or "") or ""

            items.append(
                {
                    "enrollment_id": se.enrollment_id,
                    "student_name": student_name,
                    "is_selected": se.enrollment_id in selected_ids,
                    "profile_photo_url": profile_photo_url,
                    "lecture_title": lecture_title or None,
                    "lecture_color": lecture_color or None,
                    "lecture_chip_label": lecture_chip_label or None,
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

        tenant = getattr(request, "tenant", None)

        ser = HomeworkAssignmentUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        incoming_ids = set(map(int, ser.validated_data["enrollment_ids"]))

        valid_ids = set(
            SessionEnrollment.objects
            .filter(
                tenant=tenant,
                session_id=homework.session_id,
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

        HomeworkAssignment.objects.filter(
            tenant=tenant,
            homework=homework,
        ).delete()

        HomeworkAssignment.objects.bulk_create(
            [
                HomeworkAssignment(
                    tenant=tenant,
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
