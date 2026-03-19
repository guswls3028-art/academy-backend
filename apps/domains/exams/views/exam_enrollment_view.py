# PATH: apps/domains/exams/views/exam_enrollment_view.py

from __future__ import annotations

from typing import List, Set

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status

from apps.core.permissions import TenantResolvedAndStaff

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
    - GET /api/v1/exams/{exam_id}/enrollments/?session_id=123
    - PUT /api/v1/exams/{exam_id}/enrollments/?session_id=123

    규칙:
    - session_id는 반드시 필요 (M:N 구조이므로)
    - 해당 session_id는 exam.sessions에 포함된 session만 허용
    - enrollment_ids는 반드시 "해당 세션 등록 학생"의 enrollment_id만 허용
    """

    permission_classes = [TenantResolvedAndStaff]

    def _get_session_id_or_400(self, request, exam: Exam) -> int:
        """
        ✅ M:N 구조 대응:
        - session_id는 query param으로 받는다.
        - 해당 session이 exam.sessions에 포함되지 않으면 400.
        """
        raw = request.query_params.get("session_id") or request.data.get("session_id")
        if not raw:
            raise ValueError("session_id is required")

        try:
            session_id = int(raw)
        except (TypeError, ValueError):
            raise ValueError("session_id must be integer")

        # ✅ exam.sessions에 포함된 세션인지 검증
        if hasattr(exam, "sessions"):
            ok = exam.sessions.filter(id=session_id).exists()
            if not ok:
                raise ValueError("This exam is not linked to the given session_id")
        # (legacy) 단일 session 필드가 있는 경우만 fallback
        elif hasattr(exam, "session_id"):
            if int(getattr(exam, "session_id", 0) or 0) != session_id:
                raise ValueError("This exam is not linked to the given session_id")

        return session_id

    def get(self, request, exam_id: int):
        tenant = request.tenant
        exam = get_object_or_404(
            Exam.objects.filter(
                Q(sessions__lecture__tenant=tenant)
                | Q(derived_exams__sessions__lecture__tenant=tenant)
            ).distinct(),
            pk=exam_id,
        )

        try:
            session_id = self._get_session_id_or_400(request, exam)
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        # 1) 세션 등록 학생 목록 (아바타·강의 딱지용 select_related)
        #    ✅ 퇴원(INACTIVE) 수강생 제외
        session_enrollments = (
            SessionEnrollment.objects
            .filter(session_id=session_id)
            .filter(enrollment__status="ACTIVE")
            .filter(enrollment__student__deleted_at__isnull=True)
            .select_related("enrollment", "enrollment__student", "enrollment__lecture")
            .order_by("id")
        )

        # 2) 현재 시험에 선택된 enrollment_id set
        selected_ids: Set[int] = set(
            ExamEnrollment.objects
            .filter(exam_id=exam_id)
            .values_list("enrollment_id", flat=True)
        )

        # 3) UI row 구성 (아바타·강의 딱지 포함)
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
                    student_name = str(getattr(enrollment, "student_name", "") or "")

                lecture = getattr(enrollment, "lecture", None)
                if lecture is not None:
                    lecture_title = str(getattr(lecture, "title", "") or "")
                    lecture_color = str(getattr(lecture, "color", "") or "") or "#3b82f6"
                    lecture_chip_label = str(getattr(lecture, "chip_label", "") or "") or ""

            items.append(
                {
                    "enrollment_id": int(se.enrollment_id),
                    "student_name": student_name,
                    "is_selected": int(se.enrollment_id) in selected_ids,
                    "profile_photo_url": profile_photo_url,
                    "lecture_title": lecture_title or None,
                    "lecture_color": lecture_color or None,
                    "lecture_chip_label": lecture_chip_label or None,
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
        tenant = request.tenant
        exam = get_object_or_404(
            Exam.objects.filter(
                Q(sessions__lecture__tenant=tenant)
                | Q(derived_exams__sessions__lecture__tenant=tenant)
            ).distinct(),
            pk=exam_id,
        )

        try:
            session_id = self._get_session_id_or_400(request, exam)
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

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

        # ✅ 세션 범위 내 치환 (다른 세션의 enrollment은 유지)
        ExamEnrollment.objects.filter(
            exam_id=exam_id,
            enrollment_id__in=valid_ids,
        ).delete()

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
