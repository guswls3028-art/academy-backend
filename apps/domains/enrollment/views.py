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

from apps.api.common.upload_validation import (
    DEFAULT_MAX_EXCEL_SIZE,
    EXCEL_CONTENT_TYPES,
    EXCEL_EXTENSIONS,
    validate_uploaded_file,
)
from academy.adapters.db.django import repositories_enrollment as enroll_repo
from .serializers import EnrollmentSerializer, SessionEnrollmentSerializer
from .filters import EnrollmentFilter
from django.conf import settings
from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_excel
from rest_framework.permissions import IsAuthenticated
from apps.core.permissions import TenantResolvedAndStaff
from apps.support.enrollment.view_dependencies import (
    dispatch_job,
    get_excel_parsing_job_status_response,
)
from .selectors import enrollments_for_tenant, session_enrollments_for_tenant
from .services.lifecycle import (
    bulk_create_enrollments,
    bulk_create_session_enrollments,
    delete_enrollment,
    sync_enrollment_status_side_effects,
)

logger = logging.getLogger(__name__)


class EnrollmentViewSet(ModelViewSet):
    serializer_class = EnrollmentSerializer
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_class = EnrollmentFilter
    search_fields = ["student__name"]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        return enrollments_for_tenant(tenant)

    def create(self, request, *args, **kwargs):
        return Response(
            {"detail": "수강 등록은 bulk_create 엔드포인트를 사용해야 합니다."},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    @transaction.atomic
    @action(detail=False, methods=["post"])
    def bulk_create(self, request):
        tenant = getattr(request, "tenant", None)

        lecture_id = request.data.get("lecture")
        student_ids = request.data.get("students", [])

        created = bulk_create_enrollments(
            tenant=tenant,
            lecture_id=lecture_id,
            student_ids=student_ids,
        )

        return Response(
            EnrollmentSerializer(created, many=True).data,
            status=status.HTTP_201_CREATED,
        )

    @transaction.atomic
    def perform_update(self, serializer):
        enrollment = serializer.save()
        sync_enrollment_status_side_effects(enrollment)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        enrollment = self.get_object()
        delete_enrollment(enrollment)
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
        validate_uploaded_file(
            upload_file,
            allowed_extensions=EXCEL_EXTENSIONS,
            allowed_content_types=EXCEL_CONTENT_TYPES,
            max_size=DEFAULT_MAX_EXCEL_SIZE,
            label="엑셀 파일",
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

    @action(detail=False, methods=["get"], url_path=r"excel_job_status/(?P<job_id>[^/.]+)")
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
        payload = get_excel_parsing_job_status_response(
            job_id=job_id,
            tenant_id=str(tenant.id),
        )
        if payload is None:
            raise NotFound("해당 job을 찾을 수 없습니다.")
        return Response(payload)


class SessionEnrollmentViewSet(ModelViewSet):
    serializer_class = SessionEnrollmentSerializer
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    pagination_class = None  # 차시 수강생은 bulk_create 200건 제한 — 페이지네이션 불필요, 전체 반환

    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["session", "enrollment"]
    search_fields = ["enrollment__student__name"]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        return session_enrollments_for_tenant(tenant)

    def create(self, request, *args, **kwargs):
        return Response(
            {"detail": "차시 수강 등록은 bulk_create 엔드포인트를 사용해야 합니다."},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    @transaction.atomic
    @action(detail=False, methods=["post"])
    def bulk_create(self, request):
        tenant = getattr(request, "tenant", None)

        session_id = request.data.get("session")
        enrollment_ids = request.data.get("enrollments", [])

        created = bulk_create_session_enrollments(
            tenant=tenant,
            session_id=session_id,
            enrollment_ids=enrollment_ids,
        )

        return Response(
            SessionEnrollmentSerializer(created, many=True).data,
            status=status.HTTP_201_CREATED,
        )
