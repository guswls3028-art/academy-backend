# PATH: apps/domains/attendance/views.py

import logging
from django.db import transaction
from rest_framework.viewsets import ModelViewSet
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.response import Response
from rest_framework import status
from rest_framework.exceptions import NotFound

from academy.adapters.db.django import repositories_enrollment as enroll_repo
from .models import Attendance
from .serializers import (
    AttendanceSerializer,
    AttendanceMatrixStudentSerializer,
)
from .filters import AttendanceFilter

from apps.domains.lectures.models import Lecture, Session
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.ai.gateway import dispatch_job

logger = logging.getLogger(__name__)


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
            .filter(enrollment__student__deleted_at__isnull=True)
            .select_related(
                "session",
                "session__lecture",
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

        session = enroll_repo.get_session_by_id(session_id)
        if not session:
            raise NotFound("세션을 찾을 수 없습니다.")
        created = []

        for sid in student_ids:
            enrollment, _ = enroll_repo.enrollment_get_or_create(
                tenant=tenant,
                lecture=session.lecture,
                student_id=sid,
                defaults={"status": "ACTIVE"},
            )

            enroll_repo.session_enrollment_get_or_create_tenant(
                tenant=tenant,
                session=session,
                enrollment=enrollment,
            )

            attendance, _ = enroll_repo.attendance_get_or_create_tenant(
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

        lecture = enroll_repo.get_lecture_by_id_tenant_raw(lecture_id, tenant)
        if not lecture:
            raise NotFound("강의를 찾을 수 없습니다.")

        sessions = enroll_repo.get_sessions_for_lecture_ordered(lecture)

        enrollment_ids = list(enroll_repo.get_session_enrollment_enrollment_ids(tenant, lecture))
        enrollments = enroll_repo.get_enrollments_by_ids_active(enrollment_ids, tenant)

        attendances = enroll_repo.get_attendances_for_lecture(tenant, lecture, enrollments)

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
                    "color": (lecture.color or "#3b82f6"),
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
    # 3️⃣ 엑셀 내보내기 (워커 비동기)
    # POST /api/v1/lectures/attendance/excel/ body: { "lecture_id": int }
    # 응답: { "job_id", "status": "PENDING" } → 클라이언트는 GET /api/v1/jobs/<job_id>/ 폴링 후 result.download_url 로 다운로드
    # =========================================================
    @action(detail=False, methods=["post"], url_path="excel")
    def excel(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response(
                {"detail": "tenant가 필요합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        lecture_id = request.data.get("lecture_id") or request.query_params.get("lecture")
        if not lecture_id:
            return Response(
                {"detail": "lecture_id(또는 lecture)는 필수입니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        lecture = enroll_repo.get_lecture_by_id_tenant_raw(lecture_id, tenant)
        if not lecture:
            raise NotFound("강의를 찾을 수 없습니다.")

        out = dispatch_job(
            job_type="attendance_excel_export",
            payload={
                "tenant_id": str(tenant.id),
                "lecture_id": int(lecture.id),
            },
            tenant_id=str(tenant.id),
            source_domain="attendance",
            source_id=str(lecture.id),
            tier="basic",
            idempotency_key=f"attendance_export:{tenant.id}:{lecture.id}",
        )
        if not out.get("ok"):
            return Response(
                {"detail": out.get("error", "job 등록 실패")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        logger.info(
            "ATTENDANCE_EXCEL_EXPORT dispatch job_id=%s tenant_id=%s lecture_id=%s",
            out["job_id"],
            tenant.id,
            lecture.id,
        )
        return Response(
            {"job_id": out["job_id"], "status": "PENDING"},
            status=status.HTTP_202_ACCEPTED,
        )
