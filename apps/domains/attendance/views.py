# PATH: apps/domains/attendance/views.py

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.http import HttpResponse

from rest_framework.viewsets import ModelViewSet
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.response import Response
from rest_framework import status

from .models import Attendance
from .serializers import (
    AttendanceSerializer,
    AttendanceMatrixStudentSerializer,
)
from .filters import AttendanceFilter

from apps.domains.lectures.models import Lecture, Session
from apps.domains.enrollment.models import Enrollment, SessionEnrollment

from .utils.excel import build_attendance_excel


class AttendanceViewSet(ModelViewSet):
    """
    lectures/attendance
    """

    serializer_class = AttendanceSerializer

    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_class = AttendanceFilter
    search_fields = ["enrollment__student__name"]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        return (
            Attendance.objects
            .filter(tenant=tenant)
            .select_related(
                "session",
                "enrollment",
                "enrollment__student",
            )
        )

    # =========================================================
    # 1️⃣ 세션 기준 학생 등록
    # =========================================================
    @transaction.atomic
    @action(detail=False, methods=["post"])
    def bulk_create(self, request):
        tenant = getattr(request, "tenant", None)

        session_id = request.data.get("session")
        student_ids = request.data.get("students", [])

        if not session_id or not isinstance(student_ids, list):
            return Response(
                {"detail": "session, students(list)는 필수입니다"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        session = get_object_or_404(Session, id=session_id)
        created = []

        for sid in student_ids:
            enrollment, _ = Enrollment.objects.get_or_create(
                tenant=tenant,
                student_id=sid,
                lecture=session.lecture,
                defaults={"status": "ACTIVE"},
            )

            SessionEnrollment.objects.get_or_create(
                tenant=tenant,
                enrollment=enrollment,
                session=session,
            )

            attendance, _ = Attendance.objects.get_or_create(
                tenant=tenant,
                enrollment=enrollment,
                session=session,
                defaults={"status": "PRESENT"},
            )

            created.append(attendance)

        return Response(
            AttendanceSerializer(created, many=True).data,
            status=status.HTTP_201_CREATED,
        )

    # =========================================================
    # 2️⃣ 강의 × 차시 출결 매트릭스
    # =========================================================
    @action(detail=False, methods=["get"], url_path="matrix")
    def matrix(self, request):
        tenant = getattr(request, "tenant", None)

        lecture_id = request.query_params.get("lecture")
        if not lecture_id:
            return Response(
                {"detail": "lecture 파라미터는 필수입니다"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        lecture = get_object_or_404(
            Lecture,
            id=lecture_id,
            tenant=tenant,
        )

        sessions = Session.objects.filter(
            lecture=lecture
        ).order_by("order", "id")

        enrollments = Enrollment.objects.filter(
            tenant=tenant,
            lecture=lecture,
            status="ACTIVE",
        ).select_related("student").order_by("student__name", "id")

        attendances = Attendance.objects.filter(
            tenant=tenant,
            session__lecture=lecture,
            enrollment__in=enrollments,
        )

        attendance_map = {
            (a.enrollment_id, a.session_id): a
            for a in attendances
        }

        students_payload = []

        for en in enrollments:
            row = {
                "student_id": en.student.id,
                "name": en.student.name,
                "phone": en.student.phone,
                "parent_phone": en.student.parent_phone,
                "attendance": {},
            }

            for s in sessions:
                att = attendance_map.get((en.id, s.id))
                if att:
                    row["attendance"][str(s.id)] = {
                        "attendance_id": att.id,
                        "status": att.status,
                    }

            students_payload.append(row)

        return Response(
            {
                "lecture": {
                    "id": lecture.id,
                    "title": lecture.title,
                },
                "sessions": [
                    {
                        "id": s.id,
                        "order": s.order,
                        "date": s.date,
                    }
                    for s in sessions
                ],
                "students": AttendanceMatrixStudentSerializer(
                    students_payload, many=True
                ).data,
            }
        )

    # =========================================================
    # 3️⃣ 엑셀 다운로드
    # =========================================================
    @action(detail=False, methods=["get"], url_path="excel")
    def excel(self, request):
        tenant = getattr(request, "tenant", None)

        lecture_id = request.query_params.get("lecture")
        if not lecture_id:
            return Response(
                {"detail": "lecture 파라미터는 필수입니다"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        lecture = get_object_or_404(
            Lecture,
            id=lecture_id,
            tenant=tenant,
        )

        workbook, filename = build_attendance_excel(lecture)

        response = HttpResponse(
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        workbook.save(response)
        return response
