# PATH: apps/domains/enrollment/views.py

import logging
import uuid

from django.db import transaction
from rest_framework.viewsets import ModelViewSet
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.response import Response
from rest_framework import status
from rest_framework.exceptions import ValidationError, NotFound

from academy.adapters.db.django import repositories_enrollment as enroll_repo
from .models import Enrollment, SessionEnrollment
from .serializers import EnrollmentSerializer, SessionEnrollmentSerializer
from .filters import EnrollmentFilter
from apps.domains.lectures.models import Session, Lecture
from apps.domains.students.models import Student
from apps.domains.attendance.models import Attendance
from apps.domains.ai.gateway import dispatch_job
from django.conf import settings
from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_excel

logger = logging.getLogger(__name__)


class EnrollmentViewSet(ModelViewSet):
    serializer_class = EnrollmentSerializer

    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_class = EnrollmentFilter
    search_fields = ["student__name"]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        return (
            Enrollment.objects
            .filter(tenant=tenant)
            .filter(student__deleted_at__isnull=True)
            .select_related("student", "lecture")
        )

    @transaction.atomic
    @action(detail=False, methods=["post"])
    def bulk_create(self, request):
        tenant = getattr(request, "tenant", None)

        lecture_id = request.data.get("lecture")
        student_ids = request.data.get("students", [])

        if not lecture_id or not isinstance(student_ids, list):
            return Response(
                {"detail": "lecture, students(list)는 필수입니다"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ✅ lecture tenant 검증
        lecture = enroll_repo.get_lecture_by_id_tenant(lecture_id, tenant)
        if not lecture:
            raise ValidationError({"detail": "해당 학원의 강의가 아닙니다."})

        created = []
        for sid in student_ids:
            # ✅ student tenant 검증
            if not enroll_repo.student_exists_for_tenant(sid, tenant):
                raise ValidationError(
                    {"detail": f"학생(id={sid})은 현재 학원 소속이 아닙니다."}
                )

            obj, _ = enroll_repo.enrollment_get_or_create(
                tenant=tenant,
                lecture=lecture,
                student_id=sid,
                defaults={"status": "ACTIVE"},
            )
            created.append(obj)

        return Response(
            EnrollmentSerializer(created, many=True).data,
            status=status.HTTP_201_CREATED,
        )

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        enrollment = self.get_object()

        enroll_repo.session_enrollment_filter_delete(enrollment.tenant, enrollment)

        enrollment.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["post"], url_path="lecture_enroll_from_excel")
    def lecture_enroll_from_excel(self, request):
        """
        강의 엑셀 수강등록 — 워커 전담.
        API는 파일 수신 → R2 엑셀 버킷 업로드 → SQS EXCEL_PARSING job 등록만 수행하며,
        파싱·등록 로직은 워커에서만 실행됩니다 (구조적으로 API에서 동기 처리 불가).
        POST: multipart/form-data — file (엑셀), lecture_id, initial_password
        응답: { "job_id": str } → 클라이언트는 excel_job_status 로 폴링.
        """
        request_id = str(uuid.uuid4())[:8]
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response(
                {"detail": "tenant가 필요합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        upload_file = request.FILES.get("file")
        lecture_id = request.data.get("lecture_id")
        session_id_raw = request.data.get("session_id")  # 선택: 있으면 해당 차시에만 등록
        session_id = None
        if session_id_raw not in (None, ""):
            try:
                session_id = int(session_id_raw)
            except (TypeError, ValueError):
                session_id = None
        initial_password = (request.data.get("initial_password") or "").strip()

        if not upload_file or not lecture_id:
            return Response(
                {"detail": "file(엑셀), lecture_id는 필수입니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(initial_password) < 4:
            return Response(
                {"detail": "initial_password는 4자 이상 필요합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 강의 소속 tenant 검증
        lecture = enroll_repo.get_lecture_by_id_tenant_raw(lecture_id, tenant)
        if not lecture:
            raise ValidationError({"detail": "해당 학원의 강의가 아닙니다."})

        if session_id is not None:
            session = enroll_repo.get_session_by_id_lecture(session_id, lecture)
            if not session:
                raise ValidationError({"detail": "해당 차시가 이 강의의 차시가 아닙니다."})

        # R2 엑셀 버킷에 업로드 (워커가 동일 버킷에서 다운로드)
        ext = "xlsx"
        if getattr(upload_file, "name", "") and "." in upload_file.name:
            ext = upload_file.name.rsplit(".", 1)[-1].lower() or "xlsx"
        file_key = f"excel/{tenant.id}/{uuid.uuid4().hex}.{ext}"
        bucket = getattr(settings, "R2_EXCEL_BUCKET", "academy-excel")
        upload_fileobj_to_r2_excel(
            fileobj=upload_file,
            key=file_key,
            content_type=getattr(upload_file, "content_type", None)
            or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        payload = {
            "file_key": file_key,
            "bucket": bucket,
            "tenant_id": tenant.id,
            "lecture_id": int(lecture_id),
            "initial_password": initial_password,
        }
        if session_id is not None:
            payload["session_id"] = session_id
        out = dispatch_job(
            job_type="excel_parsing",
            payload=payload,
            tenant_id=str(tenant.id),
            source_domain="enrollment",
            source_id=str(lecture_id),
            tier="basic",
            idempotency_key=f"excel:{file_key}",
        )
        if not out.get("ok"):
            return Response(
                {"detail": out.get("error", "job 등록 실패")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        logger.info(
            "EXCEL_PARSING dispatch request_id=%s job_id=%s tenant_id=%s lecture_id=%s",
            request_id,
            out["job_id"],
            tenant.id,
            lecture_id,
        )
        return Response(
            {"job_id": out["job_id"], "status": "PENDING"},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=False, methods=["get"], url_path="excel_job_status/<str:job_id>")
    def excel_job_status(self, request, job_id=None):
        """
        엑셀 수강등록(excel_parsing) job 상태 조회 (폴링용).
        GET /api/v1/enrollments/excel_job_status/<job_id>/
        공통 응답 형식: build_job_status_response (GET /api/v1/jobs/<id>/ 와 동일).
        """
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response(
                {"detail": "tenant가 필요합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        from academy.adapters.db.django.repositories_ai import DjangoAIJobRepository
        from apps.domains.ai.services.job_status_response import build_job_status_response

        repo = DjangoAIJobRepository()
        job = repo.get_job_model_for_status(job_id, str(tenant.id), job_type="excel_parsing")
        if not job:
            raise NotFound("해당 job을 찾을 수 없습니다.")
        return Response(build_job_status_response(job))


class SessionEnrollmentViewSet(ModelViewSet):
    serializer_class = SessionEnrollmentSerializer

    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["session", "enrollment"]
    search_fields = ["enrollment__student__name"]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        return (
            SessionEnrollment.objects
            .filter(tenant=tenant)
            .filter(enrollment__student__deleted_at__isnull=True)
            .select_related(
                "session",
                "enrollment",
                "enrollment__student",
            )
        )

    @transaction.atomic
    @action(detail=False, methods=["post"])
    def bulk_create(self, request):
        tenant = getattr(request, "tenant", None)

        session_id = request.data.get("session")
        enrollment_ids = request.data.get("enrollments", [])

        if not session_id or not isinstance(enrollment_ids, list):
            return Response(
                {"detail": "session, enrollments(list)는 필수입니다"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        session = enroll_repo.get_session_by_id_with_lecture(session_id)

        # ✅ session 소속 lecture tenant 검증
        if session.lecture.tenant_id != tenant.id:
            raise ValidationError({"detail": "다른 학원의 세션입니다."})

        created = []
        for eid in enrollment_ids:
            enrollment = enroll_repo.get_enrollment_by_id_with_lecture(eid, tenant)

            if enrollment.lecture_id != session.lecture_id:
                raise ValidationError(
                    {"detail": "다른 강의 수강자는 이 세션에 추가할 수 없습니다."}
                )

            obj, _ = enroll_repo.session_enrollment_get_or_create_tenant(
                tenant=tenant,
                session=session,
                enrollment=enrollment,
            )
            created.append(obj)
            # 차시 수강생 등록 시 기본 출결 행 생성 → 출결 탭에서 학생 목록이 바로 보이도록
            enroll_repo.attendance_get_or_create_tenant(
                tenant=tenant,
                enrollment=enrollment,
                session=session,
                defaults={"status": "PRESENT"},
            )

        return Response(
            SessionEnrollmentSerializer(created, many=True).data,
            status=status.HTTP_201_CREATED,
        )
