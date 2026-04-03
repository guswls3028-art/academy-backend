# PATH: apps/domains/homework/views/homework_enrollment_view.py

from __future__ import annotations

from typing import List, Set

from django.db import transaction
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status

from apps.core.permissions import TenantResolvedAndStaff

from apps.domains.enrollment.models import SessionEnrollment
from apps.domains.homework.models import HomeworkEnrollment
from apps.domains.lectures.models import Session
from apps.domains.homework.serializers.homework_enrollment_serializer import (
    HomeworkEnrollmentRowSerializer,
    HomeworkEnrollmentUpdateSerializer,
)


class HomeworkEnrollmentManageView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

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

        if not Session.objects.filter(id=session_id, lecture__tenant=tenant).exists():
            return Response({"detail": "해당 차시를 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)

        # ✅ 퇴원(INACTIVE) 수강생 제외
        session_enrollments = (
            SessionEnrollment.objects
            .filter(
                tenant=tenant,
                session_id=session_id,
            )
            .filter(enrollment__status="ACTIVE")
            .filter(enrollment__student__deleted_at__isnull=True)
            .select_related("enrollment", "enrollment__student", "enrollment__lecture")
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
        request_obj = request
        for se in session_enrollments:
            enrollment = se.enrollment
            student_name = ""
            profile_photo_url = None
            parent_phone = ""
            student_phone = ""
            school = ""
            grade = None
            lecture_title = ""
            lecture_color = ""
            lecture_chip_label = ""

            if enrollment and hasattr(enrollment, "student"):
                student = enrollment.student
                if student is not None:
                    student_name = str(getattr(student, "name", "") or "")
                    if getattr(student, "profile_photo", None):
                        try:
                            profile_photo_url = request_obj.build_absolute_uri(
                                student.profile_photo.url
                            )
                        except Exception:
                            profile_photo_url = None
                    parent_phone = getattr(student, "parent_phone", "") or ""
                    student_phone = getattr(student, "phone", "") or ""
                    school_type = getattr(student, "school_type", None)
                    if school_type == "ELEMENTARY":
                        school = getattr(student, "elementary_school", "") or ""
                    elif school_type == "HIGH":
                        school = getattr(student, "high_school", "") or ""
                    else:
                        school = getattr(student, "middle_school", "") or ""
                    grade = getattr(student, "grade", None)

                lecture = getattr(enrollment, "lecture", None)
                if lecture is not None:
                    lecture_title = str(getattr(lecture, "title", "") or "")
                    lecture_color = str(getattr(lecture, "color", "") or "") or "#3b82f6"
                    lecture_chip_label = str(getattr(lecture, "chip_label", "") or "")

            items.append(
                {
                    "enrollment_id": se.enrollment_id,
                    "student_name": student_name,
                    "is_selected": se.enrollment_id in selected_ids,
                    "profile_photo_url": profile_photo_url,
                    "lecture_title": lecture_title or None,
                    "lecture_color": lecture_color or None,
                    "lecture_chip_label": lecture_chip_label or None,
                    "parent_phone": parent_phone or None,
                    "student_phone": student_phone or None,
                    "school": school or None,
                    "grade": grade,
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

        if not Session.objects.filter(id=session_id, lecture__tenant=tenant).exists():
            return Response({"detail": "해당 차시를 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)

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
