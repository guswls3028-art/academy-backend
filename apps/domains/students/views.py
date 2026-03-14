# PATH: apps/domains/students/views.py

import logging
import traceback
import uuid

from django.db import transaction, connection, IntegrityError
from django.db.models import Q
from django.utils import timezone
from django.conf import settings
from django.contrib.auth import get_user_model

from rest_framework.viewsets import ModelViewSet
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.pagination import PageNumberPagination
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.views import APIView

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter

from apps.core.permissions import IsStudent, TenantResolvedAndStaff, TenantResolved
from apps.core.models import TenantMembership
from apps.core.models.user import user_display_username

from apps.domains.parents.services import ensure_parent_for_student
from apps.support.messaging.services import send_welcome_messages, get_site_url, send_sms, send_registration_approved_messages, enqueue_sms
from apps.domains.ai.gateway import dispatch_job
from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_excel

from academy.adapters.db.django import repositories_students as student_repo
from .models import Student, Tag, StudentTag, StudentRegistrationRequest
from .filters import StudentFilter
from .services import normalize_school_from_name
from apps.domains.enrollment.models import Enrollment
from .serializers import (
    _generate_unique_ps_number,
    StudentListSerializer,
    StudentDetailSerializer,
    TagSerializer,
    AddTagSerializer,
    StudentBulkCreateSerializer,
    RegistrationRequestCreateSerializer,
    RegistrationRequestListSerializer,
)

logger = logging.getLogger(__name__)


# ======================================================
# Tag
# ======================================================

class TagViewSet(ModelViewSet):
    """
    학생 태그 관리
    - 관리자 / 스태프 전용
    - 테넌트별 격리
    """
    serializer_class = TagSerializer
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get_queryset(self):
        return student_repo.tag_all(tenant=self.request.tenant)

    def perform_create(self, serializer):
        serializer.save(tenant=self.request.tenant)


# ======================================================
# Student
# ======================================================

class StudentListPagination(PageNumberPagination):
    """SSOT: 프론트엔드가 총 개수(count)와 results를 기대하므로 응답에 count 포함."""
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response(self, data):
        return Response({
            "count": self.page.paginator.count,
            "page_size": self.page.paginator.per_page,
            "next": self.get_next_link(),
            "previous": self.get_previous_link(),
            "results": data,
        })


class StudentViewSet(ModelViewSet):
    """
    학생 관리 ViewSet

    ✔ tenant 단위 완전 분리
    ✔ 학생 생성 시 User 계정 자동 생성
    ✔ phone = username
    ✔ 초기 비밀번호는 교사가 설정
    ✔ 학생 CRUD는 관리자만 가능

    ✅ 봉인 강화:
    - Student 생성 시 TenantMembership(role=student) 반드시 생성
    - Student 삭제 시 User 삭제(고아유저 방지)
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    pagination_class = StudentListPagination

    # ------------------------------
    # Tenant-aware QuerySet
    # ------------------------------
    def get_queryset(self):
        """
        🔐 핵심 보안 포인트
        - request.tenant 기준으로만 학생 노출
        - list: ?deleted=true 시 삭제된 학생만, 기본은 활성 학생만
        """
        qs = student_repo.student_filter_tenant(self.request.tenant)

        if self.action == "list":
            show_deleted = self.request.query_params.get("deleted") == "true"
            if show_deleted:
                qs = qs.filter(deleted_at__isnull=False)
            else:
                qs = qs.filter(deleted_at__isnull=True)
            qs = qs.prefetch_related("enrollments__lecture")
        elif self.action == "retrieve":
            qs = qs.prefetch_related("enrollments__lecture")

        return qs

    # ------------------------------
    # Serializer 선택
    # ------------------------------
    def get_serializer_class(self):
        if self.action == "create":
            from .serializers import StudentCreateSerializer
            return StudentCreateSerializer

        if self.action == "list":
            return StudentListSerializer

        if self.action in ("update", "partial_update"):
            from .serializers import StudentUpdateSerializer
            return StudentUpdateSerializer

        return StudentDetailSerializer

    # ------------------------------
    # Student + User + Membership 생성 (봉인)
    # ------------------------------
    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """
        학생 생성 시 처리 흐름

        1. 삭제된 학생 체크 (전화번호 또는 이름+학부모전화)
        2. 입력값 검증 (StudentCreateSerializer)
        3. 학부모 계정 생성/연결 (ensure_parent_for_student)
        4. User 생성 (username = ps_number)
        5. Student 생성 + tenant / user / parent 연결
        6. TenantMembership(role=student) SSOT 강제 생성
        7. (옵션) 가입 성공 메시지 일괄 발송
        """
        tenant = request.tenant
        raw_data = request.data
        name = str(raw_data.get("name", "")).strip()
        parent_phone = str(raw_data.get("parent_phone", "")).strip()
        phone = str(raw_data.get("phone", "")).strip() if raw_data.get("phone") else None

        # 삭제된 학생 체크 (전화번호 또는 이름+학부모전화)
        deleted_student = None
        if phone:
            deleted_student = student_repo.student_filter_tenant_phone_deleted(tenant, phone).first()
        if not deleted_student and name and parent_phone:
            deleted_student = student_repo.student_filter_tenant_name_parent_phone_deleted(tenant, name, parent_phone)

        if deleted_student:
            return Response(
                {
                    "code": "deleted_student_exists",
                    "detail": "삭제 대기중인 학생입니다. 복구하시겠습니까?",
                    "deleted_student": StudentDetailSerializer(deleted_student, context={"request": request}).data,
                },
                status=409,
            )

        # 활성 학생 중복 체크 (전화번호 또는 이름+학부모전화)
        active_duplicate = None
        if phone:
            active_duplicate = Student.objects.filter(
                tenant=tenant, deleted_at__isnull=True, phone=phone
            ).first()
        if not active_duplicate and name and parent_phone:
            active_duplicate = student_repo.student_filter_tenant_name_parent_phone_active(
                tenant, name, parent_phone
            )
        if active_duplicate:
            return Response(
                {
                    "code": "duplicate_student",
                    "detail": "이미 있는 학생입니다.",
                    "existing_student": StudentDetailSerializer(active_duplicate, context={"request": request}).data,
                },
                status=409,
            )

        serializer = self.get_serializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)

        User = get_user_model()
        data = serializer.validated_data
        send_welcome = data.pop("send_welcome_message", False)

        phone = data.get("phone")  # nullable
        password = data.pop("initial_password")
        parent_phone = data.get("parent_phone", "")
        ps_number = data.get("ps_number")

        # 1️⃣ 학부모 계정 생성 (ID = 학부모 전화번호)
        parent = None
        if parent_phone:
            parent = ensure_parent_for_student(
                tenant=request.tenant,
                parent_phone=parent_phone,
                student_name=data.get("name", ""),
            )

        # 2️⃣ User 생성 (tenant + 내부 username t{id}_{ps_number} 로 전역 유일)
        user = student_repo.user_create_user(
            username=ps_number,
            tenant=request.tenant,
            phone=phone or "",
            name=data.get("name", ""),
        )
        user.set_password(password)
        user.save()

        # 3️⃣ Student 생성 + parent 연결
        student = student_repo.student_create(
            tenant=request.tenant,
            user=user,
            parent=parent,
            **data,
        )

        # 4️⃣ TenantMembership
        TenantMembership.ensure_active(
            tenant=request.tenant,
            user=user,
            role="student",
        )

        # 5️⃣ 가입 성공 메시지 발송
        if send_welcome:
            site_url = get_site_url(request)
            send_welcome_messages(
                created_students=[student],
                student_password=password,
                parent_password_by_phone={parent_phone: password} if parent_phone else {},
                site_url=site_url,
            )

        output = StudentDetailSerializer(
            student,
            context={"request": request},
        )
        return Response(output.data, status=201)

    # ------------------------------
    # DELETE: 소프트 삭제 (30일 보관)
    # ------------------------------
    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        student = self.get_object()
        if student.deleted_at:
            return Response({"detail": "이미 삭제된 학생입니다."}, status=400)
        now = timezone.now()
        student.deleted_at = now
        update_fields = ["deleted_at"]
        if student.ps_number and not student.ps_number.startswith("_del_"):
            student.ps_number = f"_del_{student.id}_{student.ps_number}"
            update_fields.append("ps_number")
        if student.parent_id is not None:
            student.parent_id = None
            update_fields.append("parent")
        student.save(update_fields=update_fields)
        if student.user:
            student.user.is_active = False
            user_update = ["is_active"]
            if student.user.phone:
                student.user.phone = None
                user_update.append("phone")
            student.user.save(update_fields=user_update)
            TenantMembership.objects.filter(
                user=student.user, tenant=request.tenant
            ).update(is_active=False)
        # ✅ 소프트 삭제 시 수강등록도 비활성화
        Enrollment.objects.filter(
            student=student, tenant=request.tenant
        ).update(status="INACTIVE")
        return Response(status=204)

    # ------------------------------
    # Filtering / Searching / Ordering
    # ------------------------------
    filter_backends = [
        DjangoFilterBackend,
        SearchFilter,
        OrderingFilter,
    ]
    filterset_class = StudentFilter
    search_fields = ["ps_number", "omr_code", "name", "high_school", "middle_school", "major", "phone", "parent_phone"]
    ordering_fields = [
        "id",
        "created_at",
        "updated_at",
        "deleted_at",
        "name",
        "phone",
        "parent_phone",
        "high_school",
        "grade",
    ]
    ordering = ["-id"]

    # ------------------------------
    # Tag 관리
    # ------------------------------
    @action(detail=True, methods=["post"])
    def add_tag(self, request, pk=None):
        student = self.get_object()
        serializer = AddTagSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        tag = student_repo.tag_get(serializer.validated_data["tag_id"])
        student_repo.student_tag_get_or_create(student, tag)

        return Response({"status": "ok"}, status=201)

    @action(detail=True, methods=["post"])
    def remove_tag(self, request, pk=None):
        student = self.get_object()
        serializer = AddTagSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        student_repo.student_tag_filter_delete(student, serializer.validated_data["tag_id"])

        return Response({"status": "ok"}, status=200)

    # --------------------------------------------------
    # 엑셀 일괄 등록 (워커 전용) — 파일 업로드 → excel_parsing job
    # --------------------------------------------------
    @action(detail=False, methods=["post"], url_path="bulk_create_from_excel")
    def bulk_create_from_excel(self, request):
        """
        학생 엑셀 일괄 등록 — 워커 전담.
        POST: multipart — file (엑셀), initial_password (4자 이상).
        응답: 202 { job_id, status }.
        """
        import logging
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response(
                {"detail": "tenant가 필요합니다."},
                status=400,
            )
        upload_file = request.FILES.get("file")
        initial_password = (request.data.get("initial_password") or "").strip()
        if not upload_file:
            raise ValidationError({"detail": "file(엑셀)은 필수입니다."})
        if len(initial_password) < 4:
            raise ValidationError({"detail": "initial_password는 4자 이상 필요합니다."})

        try:
            ext = "xlsx"
            if getattr(upload_file, "name", "") and "." in upload_file.name:
                ext = upload_file.name.rsplit(".", 1)[-1].lower() or "xlsx"
            file_key = f"excel/{tenant.id}/{uuid.uuid4().hex}.{ext}"
            bucket = getattr(settings, "R2_EXCEL_BUCKET", getattr(settings, "EXCEL_BUCKET_NAME", "academy-excel"))
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
                "initial_password": initial_password,
            }
            out = dispatch_job(
                job_type="excel_parsing",
                payload=payload,
                tenant_id=str(tenant.id),
                source_domain="students",
                source_id=None,
                tier="basic",
                idempotency_key=f"excel:{file_key}",
            )
            if not out.get("ok"):
                return Response(
                    {"detail": out.get("error", "job 등록 실패")},
                    status=400,
                )
            return Response(
                {"job_id": out["job_id"], "status": "PENDING"},
                status=202,
            )
        except ValidationError:
            raise
        except Exception as e:
            logging.getLogger(__name__).exception("bulk_create_from_excel failed: %s", e)
            return Response(
                {"detail": "서버 오류가 발생했습니다.", "error": str(e)[:200]},
                status=500,
            )

    @action(detail=False, methods=["get"], url_path="excel_job_status/<str:job_id>")
    def excel_job_status(self, request, job_id=None):
        """
        엑셀 일괄등록(excel_parsing) job 상태 조회 (폴링용).
        GET /api/v1/students/excel_job_status/<job_id>/
        """
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant가 필요합니다."}, status=400)
        from academy.adapters.db.django.repositories_ai import DjangoAIJobRepository
        from apps.domains.ai.services.job_status_response import build_job_status_response

        repo = DjangoAIJobRepository()
        job = repo.get_job_model_for_status(job_id, str(tenant.id), job_type="excel_parsing")
        if not job:
            raise NotFound("해당 job을 찾을 수 없습니다.")
        return Response(build_job_status_response(job))

    # --------------------------------------------------
    # Anchor API: /students/me/ (원본 100% 유지)
    # --------------------------------------------------
    @action(
        detail=False,
        methods=["post"],
        url_path="bulk_create",
    )
    def bulk_create(self, request):
        """
        JSON 일괄 등록 (레거시·비엑셀용). 엑셀 등록은 bulk_create_from_excel + 워커 사용.
        POST body: { "initial_password": "...", "students": [ {...}, ... ] }
        """
        serializer = StudentBulkCreateSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)

        password = serializer.validated_data["initial_password"]
        students_data = serializer.validated_data["students"]
        send_welcome = serializer.validated_data.get("send_welcome_message", False)
        User = get_user_model()
        tenant = request.tenant

        created_count = 0
        failed = []
        created_students = []

        for idx, item in enumerate(students_data):
            phone = item.get("phone")  # nullable
            parent_phone = item.get("parent_phone", "")
            # ps_number: 임의 6자리 자동 부여 (학생이 추후 변경 가능)
            ps_number = _generate_unique_ps_number()
            # omr_code: 학생 전화번호가 있으면 학생 전화번호 8자리, 없으면 부모 전화번호 8자리
            if phone and len(phone) >= 8:
                omr_code = phone[-8:]
            elif parent_phone and len(parent_phone) >= 8:
                omr_code = parent_phone[-8:]
            else:
                failed.append({
                    "row": idx + 1,
                    "name": item.get("name", ""),
                    "error": "학생 전화번호 또는 부모 전화번호가 필요합니다.",
                })
                continue

            try:
                with transaction.atomic():
                    # 학생 전화번호가 있으면 중복 체크
                    if phone:
                        conflict_deleted = student_repo.student_filter_tenant_phone_deleted(
                            tenant, phone
                        ).values_list("id", flat=True).first()
                        if conflict_deleted:
                            raise ValueError("삭제된 학생과 전화번호 충돌. 복원 또는 삭제 후 재등록을 선택하세요.", conflict_deleted)
                        if student_repo.user_filter_phone_active(phone, tenant=tenant).exists():
                            raise ValueError("이미 사용 중인 전화번호입니다.")
                    if student_repo.student_filter_tenant_ps_number(tenant, ps_number).exists():
                        raise ValueError("이미 사용 중인 PS 번호입니다.")

                    # 학부모 계정 생성
                    parent = None
                    if parent_phone:
                        parent = ensure_parent_for_student(
                            tenant=tenant,
                            parent_phone=parent_phone,
                            student_name=item.get("name", ""),
                        )

                    user = student_repo.user_create_user(
                        username=ps_number,
                        tenant=tenant,
                        phone=phone or "",
                        name=item.get("name", ""),
                    )
                    user.set_password(password)
                    user.save()

                    school_val = (item.get("school") or "").strip() or None
                    st, high_school, middle_school = normalize_school_from_name(
                        school_val, item.get("school_type")
                    )
                    high_school_class = (item.get("high_school_class") or "").strip() or None if st == "HIGH" else None
                    major = (item.get("major") or "").strip() or None if st == "HIGH" else None

                    student = student_repo.student_create(
                        tenant=tenant,
                        user=user,
                        parent=parent,
                        name=item["name"],
                        phone=phone,
                        parent_phone=item["parent_phone"],
                        ps_number=ps_number,
                        omr_code=omr_code,
                        uses_identifier=item.get("uses_identifier", False) or (phone is None),
                        gender=item.get("gender") or None,
                        school_type=st,
                        high_school=high_school,
                        middle_school=middle_school,
                        high_school_class=high_school_class,
                        major=major,
                        grade=item.get("grade"),
                        memo=item.get("memo") or None,
                        is_managed=item.get("is_managed", True),
                    )

                    TenantMembership.ensure_active(
                        tenant=tenant,
                        user=user,
                        role="student",
                    )
                    created_count += 1
                    created_students.append(student)
            except Exception as e:
                err_msg = str(e)
                conflict_student_id = None
                if isinstance(e, ValueError) and len(e.args) >= 2:
                    conflict_student_id = e.args[1]
                    err_msg = e.args[0]
                failed.append({
                    "row": idx + 1,
                    "name": item.get("name", ""),
                    "error": err_msg,
                    "conflict_student_id": conflict_student_id,
                })

        if send_welcome and created_students:
            site_url = get_site_url(request)
            parent_pw = {s.parent_phone: password for s in created_students if getattr(s, "parent_phone", None)}
            send_welcome_messages(
                created_students=created_students,
                student_password=password,
                parent_password_by_phone=parent_pw,
                site_url=site_url,
            )

        return Response({
            "created": created_count,
            "failed": failed,
            "total": len(students_data),
        }, status=201)

    @action(
        detail=False,
        methods=["post"],
        url_path="bulk_resolve_conflicts",
    )
    def bulk_resolve_conflicts(self, request):
        """
        충돌 해결 후 재시도 — 삭제된 학생과 번호 충돌 시 복원 또는 영구 삭제 후 재등록
        POST body: {
          "initial_password": "...",
          "send_welcome_message": false,
          "resolutions": [ { "row": 1, "student_id": 123, "action": "restore"|"delete", "student_data": {...} } ]
        }
        """
        password = request.data.get("initial_password") or ""
        if len(str(password)) < 4:
            return Response({"detail": "초기 비밀번호는 4자 이상이어야 합니다."}, status=400)
        send_welcome = request.data.get("send_welcome_message", False)
        resolutions = request.data.get("resolutions") or []
        if not isinstance(resolutions, (list, tuple)):
            return Response({"detail": "resolutions는 배열이어야 합니다."}, status=400)

        tenant = request.tenant
        User = get_user_model()
        created_count = 0
        restored_count = 0
        failed = []
        created_students = []

        for r in resolutions:
            row = r.get("row")
            student_id = r.get("student_id")
            action = r.get("action")
            student_data = r.get("student_data") or {}
            if not student_id or action not in ("restore", "delete"):
                failed.append({"row": row, "name": student_data.get("name", ""), "error": "잘못된 resolution"})
                continue

            try:
                student = student_repo.student_filter_tenant_id_deleted_first(tenant, student_id)
                if not student:
                    failed.append({"row": row, "name": student_data.get("name", ""), "error": "삭제된 학생을 찾을 수 없습니다."})
                    continue

                if action == "restore":
                    with transaction.atomic():
                        student.deleted_at = None
                        student.name = (student_data.get("name") or student.name or "").strip()
                        school_val = (student_data.get("school") or "").strip() or None
                        st, high_school, middle_school = normalize_school_from_name(
                            school_val, student_data.get("school_type")
                        )
                        student.school_type = st
                        student.high_school = high_school
                        student.middle_school = middle_school
                        student.high_school_class = (student_data.get("high_school_class") or "").strip() or None if st == "HIGH" else None
                        student.major = (student_data.get("major") or "").strip() or None if st == "HIGH" else None
                        student.gender = student_data.get("gender") or None
                        student.grade = student_data.get("grade")
                        student.memo = (student_data.get("memo") or "") or None
                        student.uses_identifier = student_data.get("uses_identifier", False)
                        student.save()
                        if student.user:
                            student.user.is_active = True
                            student.user.save(update_fields=["is_active"])
                        TenantMembership.ensure_active(tenant=tenant, user=student.user, role="student")
                        # 복원 시 이전 수강등록은 재활성화하지 않음 (이전 이력이 유령 복원되는 것 방지)
                    restored_count += 1
                    created_students.append(student)
                else:
                    with transaction.atomic():
                        student_repo.enrollment_filter_student_delete(student.id)
                        if student.user_id:
                            student.user.delete()
                        else:
                            student.delete()
                    parent = None
                    parent_phone_raw = str(student_data.get("parent_phone") or student_data.get("parentPhone", "")).replace(" ", "").replace("-", "").replace(".", "")
                    parent_phone = parent_phone_raw if len(parent_phone_raw) >= 11 else ""
                    if parent_phone:
                        parent = ensure_parent_for_student(
                            tenant=tenant,
                            parent_phone=parent_phone,
                            student_name=student_data.get("name", ""),
                        )
                    phone_raw = str(student_data.get("phone", "")).replace(" ", "").replace("-", "").replace(".", "")
                    phone = phone_raw if phone_raw and len(phone_raw) == 11 and phone_raw.startswith("010") else None
                    parent_phone_val = student_data.get("parent_phone") or student_data.get("parentPhone", "")
                    parent_phone = str(parent_phone_val).replace(" ", "").replace("-", "").replace(".", "")
                    # ps_number: 임의 6자리 자동 부여
                    ps_number = _generate_unique_ps_number()
                    # omr_code: 학생 전화번호가 있으면 학생 전화번호 8자리, 없으면 부모 전화번호 8자리
                    if phone and len(phone) >= 8:
                        omr_code = phone[-8:]
                    elif parent_phone and len(parent_phone) >= 8:
                        omr_code = parent_phone[-8:]
                    else:
                        raise ValueError("학생 전화번호 또는 부모 전화번호가 필요합니다.")
                    user = student_repo.user_create_user(
                        username=ps_number,
                        tenant=tenant,
                        phone=phone or "",
                        name=student_data.get("name", ""),
                    )
                    user.set_password(password)
                    user.save()
                    school_val = (student_data.get("school") or "").strip() or None
                    st, high_school, middle_school = normalize_school_from_name(
                        school_val, student_data.get("school_type")
                    )
                    high_school_class = (student_data.get("high_school_class") or "").strip() or None if st == "HIGH" else None
                    major = (student_data.get("major") or "").strip() or None if st == "HIGH" else None
                    new_student = student_repo.student_create(
                        tenant=tenant,
                        user=user,
                        parent=parent,
                        name=student_data.get("name", ""),
                        phone=phone,
                        parent_phone=parent_phone,
                        ps_number=ps_number,
                        omr_code=omr_code,
                        uses_identifier=student_data.get("uses_identifier", False) or (phone is None),
                        gender=student_data.get("gender") or None,
                        school_type=st,
                        high_school=high_school,
                        middle_school=middle_school,
                        high_school_class=high_school_class,
                        major=major,
                        grade=student_data.get("grade"),
                        memo=student_data.get("memo") or None,
                        is_managed=student_data.get("is_managed", True),
                    )
                    TenantMembership.ensure_active(tenant=tenant, user=user, role="student")
                    created_count += 1
                    created_students.append(new_student)
            except Exception as e:
                failed.append({"row": row, "name": student_data.get("name", ""), "error": str(e)})

        if send_welcome and created_students:
            site_url = get_site_url(request)
            parent_pw = {s.parent_phone: password for s in created_students if getattr(s, "parent_phone", None)}
            send_welcome_messages(
                created_students=created_students,
                student_password=password,
                parent_password_by_phone=parent_pw,
                site_url=site_url,
            )

        return Response({
            "created": created_count,
            "restored": restored_count,
            "failed": failed,
        }, status=200)

    @action(
        detail=False,
        methods=["post"],
        url_path="bulk_delete",
    )
    def bulk_delete(self, request):
        """
        선택 학생 일괄 소프트 삭제 (30일 보관)
        POST body: { "ids": [1, 2, 3, ...] }
        """
        ids = request.data.get("ids") or []
        if not isinstance(ids, (list, tuple)):
            return Response({"detail": "ids는 배열이어야 합니다."}, status=400)
        ids = [int(x) for x in ids if isinstance(x, (int, str)) and str(x).isdigit()]
        if not ids:
            return Response({"detail": "삭제할 ID가 없습니다."}, status=400)

        tenant = request.tenant
        to_delete = list(student_repo.student_filter_tenant_ids_active(tenant, ids))
        now = timezone.now()
        with transaction.atomic():
            for student in to_delete:
                student.deleted_at = now
                update_fields = ["deleted_at"]
                if student.ps_number and not student.ps_number.startswith("_del_"):
                    student.ps_number = f"_del_{student.id}_{student.ps_number}"
                    update_fields.append("ps_number")
                if student.parent_id is not None:
                    student.parent_id = None
                    update_fields.append("parent")
                student.save(update_fields=update_fields)
                if student.user:
                    student.user.is_active = False
                    user_update = ["is_active"]
                    if student.user.phone:
                        student.user.phone = None
                        user_update.append("phone")
                    student.user.save(update_fields=user_update)
                    TenantMembership.objects.filter(
                        user=student.user, tenant=tenant
                    ).update(is_active=False)
                # ✅ 소프트 삭제 시 수강등록도 비활성화
                Enrollment.objects.filter(
                    student=student, tenant=tenant
                ).update(status="INACTIVE")
        return Response({"deleted": len(to_delete)}, status=200)

    @action(
        detail=False,
        methods=["post"],
        url_path="bulk_restore",
    )
    def bulk_restore(self, request):
        """
        삭제된 학생 일괄 복원
        POST body: { "ids": [1, 2, 3, ...] }
        """
        ids = request.data.get("ids") or []
        if not isinstance(ids, (list, tuple)):
            return Response({"detail": "ids는 배열이어야 합니다."}, status=400)
        ids = [int(x) for x in ids if isinstance(x, (int, str)) and str(x).isdigit()]
        if not ids:
            return Response({"detail": "복원할 ID가 없습니다."}, status=400)

        tenant = request.tenant
        to_restore = list(student_repo.student_filter_tenant_ids_deleted(tenant, ids))
        with transaction.atomic():
            for student in to_restore:
                student.deleted_at = None
                update_fields = ["deleted_at"]
                if student.ps_number and student.ps_number.startswith("_del_"):
                    parts = student.ps_number.split("_", 3)
                    if len(parts) >= 4:
                        student.ps_number = parts[3]
                        update_fields.append("ps_number")
                student.save(update_fields=update_fields)
                if student.user:
                    student.user.is_active = True
                    student.user.save(update_fields=["is_active"])
                    TenantMembership.ensure_active(
                        tenant=tenant, user=student.user, role="student"
                    )
                # 복원 시 이전 수강등록은 재활성화하지 않음 (유령 복원 방지)
        return Response({"restored": len(to_restore)}, status=200)

    @action(
        detail=False,
        methods=["post"],
        url_path="bulk_permanent_delete",
    )
    def bulk_permanent_delete(self, request):
        """
        삭제된 학생 즉시 영구 삭제
        POST body: { "ids": [1, 2, 3, ...] }
        """
        ids = request.data.get("ids") or []
        if not isinstance(ids, (list, tuple)):
            return Response({"detail": "ids는 배열이어야 합니다."}, status=400)
        ids = [int(x) for x in ids if isinstance(x, (int, str)) and str(x).isdigit()]
        if not ids:
            return Response({"detail": "삭제할 ID가 없습니다."}, status=400)

        tenant = request.tenant
        to_delete = list(student_repo.student_filter_tenant_ids_deleted(tenant, ids))
        if not to_delete:
            return Response({"deleted": 0}, status=200)

        student_ids = [s.id for s in to_delete]
        user_ids = [s.user_id for s in to_delete if s.user_id]
        deleted = 0
        logger.info(
            "bulk_permanent_delete start tenant_id=%s student_ids=%s user_ids=%s",
            getattr(tenant, "id", None), student_ids, user_ids,
        )
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    # enrollment ID를 먼저 명시적으로 수집
                    cursor.execute(
                        "SELECT id FROM enrollment_enrollment WHERE student_id IN %s",
                        [tuple(student_ids)],
                    )
                    enrollment_ids = [row[0] for row in cursor.fetchall()]
                    logger.info(
                        "bulk_permanent_delete enrollment_ids=%s (count=%s)",
                        enrollment_ids[:20], len(enrollment_ids),
                    )

                    if enrollment_ids:
                        e_ids = tuple(enrollment_ids)

                        # 1) results / submissions / homework (enrollment_id 기반)
                        for tbl, where_sql in [
                            (
                                "results_result_item",
                                "result_id IN (SELECT id FROM results_result WHERE enrollment_id IN %s)",
                            ),
                            ("results_result", "enrollment_id IN %s"),
                            ("results_exam_attempt", "enrollment_id IN %s"),
                            ("results_fact", "enrollment_id IN %s"),
                            ("results_wrong_note_pdf", "enrollment_id IN %s"),
                            (
                                "results_exam_result",
                                "submission_id IN (SELECT id FROM submissions_submission WHERE enrollment_id IN %s)",
                            ),
                            (
                                "submissions_submissionanswer",
                                "submission_id IN (SELECT id FROM submissions_submission WHERE enrollment_id IN %s)",
                            ),
                            ("submissions_submission", "enrollment_id IN %s"),
                            ("homework_results_homeworkscore", "enrollment_id IN %s"),
                            ("homework_assignment", "enrollment_id IN %s"),
                            ("homework_enrollment", "enrollment_id IN %s"),
                        ]:
                            cursor.execute(
                                "SELECT 1 FROM information_schema.tables "
                                "WHERE table_schema = %s AND table_name = %s",
                                ["public", tbl],
                            )
                            if cursor.fetchone():
                                logger.info("bulk_permanent_delete DELETE %s", tbl)
                                cursor.execute(f"DELETE FROM {tbl} WHERE {where_sql}", [e_ids])

                        # 2) enrollment 자식 테이블들 (enrollment_id FK)
                        enrollment_child_tables = [
                            "attendance_attendance",
                            "enrollment_sessionenrollment",
                            "exams_exam_enrollment",
                            "video_videopermission",
                            "video_videoprogress",
                            "video_videoplaybacksession",
                            "video_videoplaybackevent",
                            "progress_sessionprogress",
                            "progress_lectureprogress",
                            "progress_cliniclink",
                            "progress_risklog",
                        ]
                        for tbl in enrollment_child_tables:
                            cursor.execute(
                                "SELECT 1 FROM information_schema.tables "
                                "WHERE table_schema = %s AND table_name = %s",
                                ["public", tbl],
                            )
                            if cursor.fetchone():
                                logger.info("bulk_permanent_delete DELETE %s", tbl)
                                cursor.execute(
                                    f"DELETE FROM {tbl} WHERE enrollment_id IN %s",
                                    [e_ids],
                                )

                    # 3) enrollment 자체 삭제
                    logger.info("bulk_permanent_delete DELETE enrollment_enrollment")
                    cursor.execute(
                        "DELETE FROM enrollment_enrollment WHERE student_id IN %s",
                        [tuple(student_ids)],
                    )
                    logger.info("bulk_permanent_delete DELETE students_studenttag")
                    cursor.execute(
                        "DELETE FROM students_studenttag WHERE student_id IN %s",
                        [tuple(student_ids)],
                    )
                    cursor.execute(
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE table_schema = %s AND table_name = %s",
                        ["public", "students_studentregistrationrequest"],
                    )
                    if cursor.fetchone():
                        logger.info("bulk_permanent_delete UPDATE students_studentregistrationrequest (unlink)")
                        cursor.execute(
                            "UPDATE students_studentregistrationrequest SET student_id = NULL WHERE student_id IN %s",
                            [tuple(student_ids)],
                        )
                    for tbl in [
                        "clinic_sessionparticipant",
                        "clinic_submission",
                        "video_videocomment",
                        "video_videolike",
                    ]:
                        cursor.execute(
                            "SELECT 1 FROM information_schema.tables "
                            "WHERE table_schema = %s AND table_name = %s",
                            ["public", tbl],
                        )
                        if cursor.fetchone():
                            logger.info("bulk_permanent_delete DELETE %s", tbl)
                            # video_videocomment uses author_student_id, others use student_id
                            col = "author_student_id" if tbl == "video_videocomment" else "student_id"
                            cursor.execute(
                                f"DELETE FROM {tbl} WHERE {col} IN %s",
                                [tuple(student_ids)],
                            )
                    # 커뮤니티(QnA 등)가 해당 학생을 created_by로 참조 → FK 해제 (SET_NULL과 동일)
                    for tbl in ["community_postentity", "community_postreply"]:
                        cursor.execute(
                            "SELECT 1 FROM information_schema.tables "
                            "WHERE table_schema = %s AND table_name = %s",
                            ["public", tbl],
                        )
                        if cursor.fetchone():
                            logger.info("bulk_permanent_delete UPDATE %s (unlink created_by)", tbl)
                            cursor.execute(
                                f"UPDATE {tbl} SET created_by_id = NULL WHERE created_by_id IN %s",
                                [tuple(student_ids)],
                            )
                    logger.info("bulk_permanent_delete DELETE students_student")
                    cursor.execute(
                        "DELETE FROM students_student WHERE id IN %s",
                        [tuple(student_ids)],
                    )
                    if user_ids:
                        # submissions_submission.user_id → accounts_user. enrollment 외 제출도 있을 수 있으므로 user_id 기준 정리.
                        cursor.execute(
                            "SELECT 1 FROM information_schema.tables "
                            "WHERE table_schema = %s AND table_name = %s",
                            ["public", "submissions_submission"],
                        )
                        if cursor.fetchone():
                            sub_ids_sql = "SELECT id FROM submissions_submission WHERE user_id IN %s"
                            cursor.execute(
                                "SELECT 1 FROM information_schema.tables "
                                "WHERE table_schema = %s AND table_name = %s",
                                ["public", "results_exam_result"],
                            )
                            if cursor.fetchone():
                                logger.info("bulk_permanent_delete DELETE results_exam_result (by user submissions)")
                                cursor.execute(
                                    f"DELETE FROM results_exam_result WHERE submission_id IN ({sub_ids_sql})",
                                    [tuple(user_ids)],
                                )
                            cursor.execute(
                                "SELECT 1 FROM information_schema.tables "
                                "WHERE table_schema = %s AND table_name = %s",
                                ["public", "submissions_submissionanswer"],
                            )
                            if cursor.fetchone():
                                logger.info("bulk_permanent_delete DELETE submissions_submissionanswer (by user)")
                                cursor.execute(
                                    f"DELETE FROM submissions_submissionanswer WHERE submission_id IN ({sub_ids_sql})",
                                    [tuple(user_ids)],
                                )
                            logger.info("bulk_permanent_delete DELETE submissions_submission (by user_id)")
                            cursor.execute(
                                "DELETE FROM submissions_submission WHERE user_id IN %s",
                                [tuple(user_ids)],
                            )
                        for tbl in ["core_attendance", "core_expense"]:
                            cursor.execute(
                                "SELECT 1 FROM information_schema.tables "
                                "WHERE table_schema = %s AND table_name = %s",
                                ["public", tbl],
                            )
                            if cursor.fetchone():
                                logger.info("bulk_permanent_delete DELETE %s", tbl)
                                cursor.execute(
                                    f"DELETE FROM {tbl} WHERE user_id IN %s",
                                    [tuple(user_ids)],
                                )
                        logger.info("bulk_permanent_delete DELETE core_tenantmembership, accounts_user")
                        cursor.execute(
                            "DELETE FROM core_tenantmembership WHERE user_id IN %s",
                            [tuple(user_ids)],
                        )
                        cursor.execute(
                            "DELETE FROM accounts_user WHERE id IN %s",
                            [tuple(user_ids)],
                        )
                    deleted = len(to_delete)
        except Exception as e:
            logger.exception(
                "bulk_permanent_delete failed: %s (student_ids=%s)",
                e, student_ids,
            )
            detail = str(e)
            if settings.DEBUG:
                detail += "\n" + traceback.format_exc()
            return Response(
                {"detail": f"영구 삭제 중 오류: {detail}"},
                status=500,
            )
        return Response({"deleted": deleted}, status=200)

    @action(
        detail=False,
        methods=["get"],
        url_path="deleted_duplicates_check",
    )
    def deleted_duplicates_check(self, request):
        """
        삭제된 학생 중 (이름+학부모전화) 중복 검사 — 고객 셀프 복구용.
        GET → { "duplicate_groups": int, "records_to_remove": int }
        """
        from django.db.models import Count, Min

        tenant = request.tenant
        dup_groups = student_repo.student_filter_tenant_deleted_dup_groups(tenant)
        groups_list = list(dup_groups)
        records_to_remove = sum(g["cnt"] - 1 for g in groups_list)
        return Response({
            "duplicate_groups": len(groups_list),
            "records_to_remove": records_to_remove,
        })

    @action(
        detail=False,
        methods=["post"],
        url_path="deleted_duplicates_fix",
    )
    def deleted_duplicates_fix(self, request):
        """
        삭제된 학생 중 (이름+학부모전화) 중복 정리 — 그룹당 1명만 유지, 나머지 영구 삭제.
        POST → { "removed": int }
        """
        tenant = request.tenant
        dup_groups = student_repo.student_filter_tenant_deleted_dup_groups(tenant)
        groups_list = list(dup_groups)
        if not groups_list:
            return Response({"removed": 0}, status=200)

        removed = 0
        with transaction.atomic():
            for g in groups_list:
                keep = student_repo.student_filter_dup_keep_first(
                    g["tenant_id"], g["name"], g["parent_phone"]
                )
                to_remove = list(
                    student_repo.student_filter_dup_to_remove(
                        g["tenant_id"], g["name"], g["parent_phone"], keep.id
                    )
                )
                for s in to_remove:
                    student_repo.enrollment_filter_student_delete_obj(s)
                    user = s.user
                    s.delete()
                    if user:
                        user.delete()
                    removed += 1
        return Response({"removed": removed}, status=200)

    @action(
        detail=False,
        methods=["get", "patch"],
        url_path="me",
        permission_classes=[IsAuthenticated, IsStudent],
    )
    def me(self, request):
        """
        학생 본인 정보 조회 + 수정 (Anchor API)

        🔒 보안 포인트
        - request.user + request.tenant 기준 강제
        - 다른 학원 / 다른 학생 접근 불가
        """
        student = student_repo.student_get_tenant_user(request.tenant, request.user)

        if request.method == "GET":
            serializer = StudentDetailSerializer(
                student,
                context={"request": request},
            )
            return Response(serializer.data)

        # PATCH: 프로필 수정 (아이디 변경, 비밀번호 변경, 기본정보 수정)
        data = request.data
        tenant = request.tenant
        user = student.user

        # --- 기본 정보 필드 유효성 검증 (setattr 전에 수행) ---
        VALID_SCHOOL_TYPES = {"MIDDLE", "HIGH"}
        VALID_GRADES = {1, 2, 3}
        STRING_FIELD_LIMITS = {
            "name": 100, "phone": 20, "parent_phone": 20,
            "gender": 10, "address": 255, "high_school": 100,
            "middle_school": 100, "origin_middle_school": 100,
            "high_school_class": 100, "major": 50, "memo": None,
        }

        if "school_type" in data and data["school_type"] not in VALID_SCHOOL_TYPES:
            return Response(
                {"detail": f"school_type은 {VALID_SCHOOL_TYPES} 중 하나여야 합니다."},
                status=400,
            )
        if "grade" in data and data["grade"] is not None:
            try:
                grade_val = int(data["grade"])
            except (TypeError, ValueError):
                return Response(
                    {"detail": "grade는 정수여야 합니다."}, status=400,
                )
            if grade_val not in VALID_GRADES:
                return Response(
                    {"detail": f"grade는 {sorted(VALID_GRADES)} 중 하나여야 합니다."},
                    status=400,
                )
        for field, max_len in STRING_FIELD_LIMITS.items():
            if field in data and data[field] is not None:
                if not isinstance(data[field], str):
                    return Response(
                        {"detail": f"{field}은(는) 문자열이어야 합니다."},
                        status=400,
                    )
                if max_len and len(data[field]) > max_len:
                    return Response(
                        {"detail": f"{field}은(는) {max_len}자 이하여야 합니다."},
                        status=400,
                    )

        with transaction.atomic():
            # 아이디 변경
            new_username = (data.get("username") or "").strip()
            if new_username and new_username != user_display_username(user):
                from apps.core.models.user import user_internal_username
                internal = user_internal_username(tenant, new_username)
                # 테넌트 내 중복 검사 (다른 테넌트는 같은 아이디 허용)
                if get_user_model().objects.filter(username=internal).exclude(id=user.id).exists():
                    return Response(
                        {"detail": "이미 사용 중인 아이디입니다."},
                        status=400,
                    )
                user.username = internal
                user.save(update_fields=["username"])
                # ps_number도 동기화
                student.ps_number = new_username
                student.save(update_fields=["ps_number"])

            # 비밀번호 변경
            current_pw = (data.get("current_password") or "").strip()
            new_pw = (data.get("new_password") or "").strip()
            if current_pw and new_pw:
                if not user.check_password(current_pw):
                    return Response(
                        {"detail": "현재 비밀번호가 일치하지 않습니다."},
                        status=400,
                    )
                if len(new_pw) < 4:
                    return Response(
                        {"detail": "새 비밀번호는 4자 이상이어야 합니다."},
                        status=400,
                    )
                user.set_password(new_pw)
                user.save(update_fields=["password"])

            # 프로필 사진
            if "profile_photo" in request.FILES:
                student.profile_photo = request.FILES["profile_photo"]
                student.save(update_fields=["profile_photo"])

            # 기본 정보 수정
            updatable_fields = [
                "name", "phone", "parent_phone", "gender", "address",
                "school_type", "high_school", "middle_school",
                "origin_middle_school", "grade", "high_school_class",
                "major", "memo",
            ]
            update_fields = []
            for field in updatable_fields:
                if field in data:
                    value = data[field]
                    # grade: 정수 변환 (검증은 위에서 완료)
                    if field == "grade" and value is not None:
                        value = int(value)
                    setattr(student, field, value)
                    update_fields.append(field)
            if update_fields:
                student.save(update_fields=update_fields)

        serializer = StudentDetailSerializer(
            student,
            context={"request": request},
        )
        return Response(serializer.data)


# ======================================================
# 학생 가입 신청 (로그인 전 회원가입 → 선생 승인)
# ======================================================


def _approve_registration_request(request, reg):
    """
    가입 신청 1건 승인 처리. 성공 시 None 반환, 실패 시 Response 반환.
    호출 후 reg.student 가 설정됨.
    """
    from apps.core.models.user import user_internal_username

    tenant = request.tenant
    # 학부모 비밀번호는 항상 "0000" 고정
    parent_fixed_password = "0000"
    name = reg.name
    parent_phone = reg.parent_phone
    phone = reg.phone

    try:
        ps_number = _generate_unique_ps_number(tenant=tenant)
    except ValueError as e:
        return Response({"detail": str(e)}, status=400)

    requested_username = (reg.username or "").strip()
    if requested_username:
        internal = user_internal_username(tenant, requested_username)
        if get_user_model().objects.filter(username=internal).exists():
            requested_username = None
    if not requested_username:
        requested_username = ps_number

    if phone and len(str(phone)) >= 8:
        omr_code = str(phone)[-8:]
    elif parent_phone and len(parent_phone) >= 8:
        omr_code = parent_phone[-8:]
    else:
        omr_code = (parent_phone or "00000000")[-8:]

    try:
        with transaction.atomic():
            parent = None
            if parent_phone:
                parent = ensure_parent_for_student(
                    tenant=tenant,
                    parent_phone=parent_phone,
                    student_name=name,
                )
            User = get_user_model()
            user = student_repo.user_create_user(
                username=requested_username,
                tenant=tenant,
                phone=phone or "",
                name=name,
            )
            # 가입 시 입력한 비밀번호(해시) 직접 이전 — 임시 비번 생성하지 않음
            user.password = reg.initial_password
            user.save()

            student = student_repo.student_create(
                tenant=tenant,
                user=user,
                parent=parent,
                name=name,
                parent_phone=parent_phone,
                phone=phone,
                ps_number=ps_number,
                omr_code=omr_code,
                uses_identifier=not (phone and phone.strip()),
                school_type=reg.school_type,
                high_school=reg.high_school or None,
                middle_school=reg.middle_school or None,
                high_school_class=reg.high_school_class or None,
                major=reg.major or None,
                grade=reg.grade,
                gender=reg.gender or None,
                memo=reg.memo or None,
                address=reg.address or None,
                origin_middle_school=reg.origin_middle_school or None,
            )
            TenantMembership.ensure_active(tenant=tenant, user=user, role="student")
            reg.status = StudentRegistrationRequest.APPROVED
            reg.student = student
            reg.save(update_fields=["status", "student", "updated_at"])

        send_registration_approved_messages(
            tenant_id=tenant.id,
            site_url=get_site_url(request) or "",
            student_name=name,
            student_phone=(phone or "") if phone else "",
            student_id=requested_username,
            student_password="가입 시 입력한 비밀번호",
            parent_phone=parent_phone or "",
            parent_password=parent_fixed_password,
        )
        return None
    except Exception as e:
        logger.exception("_approve_registration_request error: %s", e)
        return Response(
            {"detail": str(e) if settings.DEBUG else "승인 처리 중 오류가 발생했습니다."},
            status=500,
        )


class RegistrationRequestViewSet(ModelViewSet):
    """
    - create: AllowAny + TenantResolved (학생이 로그인 페이지에서 신청)
    - list / retrieve / approve: TenantResolvedAndStaff (선생이 가입신청 목록/승인)
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    serializer_class = RegistrationRequestListSerializer
    pagination_class = StudentListPagination

    def get_queryset(self):
        return StudentRegistrationRequest.objects.filter(
            tenant=self.request.tenant
        ).select_related("tenant", "student").order_by("-created_at")

    def filter_queryset(self, queryset):
        action = getattr(self, "action", None)
        if action == "list":
            status = self.request.query_params.get("status")
            if status in (StudentRegistrationRequest.PENDING, StudentRegistrationRequest.APPROVED, StudentRegistrationRequest.REJECTED):
                queryset = queryset.filter(status=status)
        return queryset

    def get_authenticators(self):
        # create/check_duplicate는 비로그인 요청 허용 → JWT 검사 생략 (만료 토큰 시 401 방지)
        if getattr(self, "action", None) in ("create", "check_duplicate"):
            return []
        return super().get_authenticators()

    def get_permissions(self):
        if getattr(self, "action", None) in ("create", "check_duplicate"):
            return [AllowAny(), TenantResolved()]
        return [IsAuthenticated(), TenantResolvedAndStaff()]

    def get_serializer_class(self):
        if getattr(self, "action", None) == "create":
            return RegistrationRequestCreateSerializer
        return RegistrationRequestListSerializer

    @action(detail=False, methods=["post"], url_path="check_duplicate")
    def check_duplicate(self, request):
        """
        회원가입 실시간 중복검사.
        POST body: { "username": "abc", "phone": "01012345678" }
        둘 다 선택적 — 입력된 필드만 검사.
        """
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "테넌트를 확인할 수 없습니다."}, status=403)

        username = (request.data.get("username") or "").strip()
        phone = (request.data.get("phone") or "").strip().replace("-", "")

        result = {}

        if username:
            # 1) 이미 등록된 학생 (활성)
            exists = Student.objects.filter(
                tenant=tenant,
                deleted_at__isnull=True,
                user__username__endswith=f"_{username}",
            ).exists()
            if exists:
                result["username"] = {"available": False, "reason": "이미 사용 중인 아이디입니다."}
            else:
                # 2) 승인 대기 중인 신청
                pending = StudentRegistrationRequest.objects.filter(
                    tenant=tenant,
                    status=StudentRegistrationRequest.PENDING,
                    username=username,
                ).exists()
                if pending:
                    result["username"] = {"available": False, "reason": "승인 대기 중인 아이디입니다. 선생님의 승인을 기다려 주세요."}
                else:
                    result["username"] = {"available": True}

        if phone and len(phone) == 11:
            # 1) 이미 등록된 학생 (활성)
            existing = Student.objects.filter(
                tenant=tenant,
                deleted_at__isnull=True,
            ).filter(
                Q(phone=phone) | Q(parent_phone=phone)
            ).first()
            if existing:
                result["phone"] = {
                    "available": False,
                    "reason": "이미 등록된 전화번호입니다. 기존 계정으로 로그인해 주세요.",
                }
            else:
                # 2) 승인 대기 중인 신청
                pending = StudentRegistrationRequest.objects.filter(
                    tenant=tenant,
                    status=StudentRegistrationRequest.PENDING,
                ).filter(
                    Q(phone=phone) | Q(parent_phone=phone)
                ).exists()
                if pending:
                    result["phone"] = {"available": False, "reason": "해당 전화번호로 가입 신청이 승인 대기 중입니다."}
                else:
                    result["phone"] = {"available": True}

        return Response(result)

    def create(self, request, *args, **kwargs):
        if not getattr(request, "tenant", None):
            return Response(
                {"detail": "테넌트를 확인할 수 없습니다. 로그인 URL(테넌트 코드 포함)로 접속했는지 확인해 주세요."},
                status=403,
            )
        serializer = self.get_serializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        # 중복 가입 체크: 전화번호 또는 username이 이미 활성 학생으로 존재하는 경우
        data_check = serializer.validated_data
        phone_check = (data_check.get("phone") or "").strip()
        username_check = (data_check.get("username") or "").strip()
        tenant = request.tenant

        existing_student = None
        if phone_check:
            existing_student = Student.objects.filter(
                tenant=tenant,
                deleted_at__isnull=True,
            ).filter(
                Q(phone=phone_check) | Q(parent_phone=phone_check)
            ).select_related("user").first()
        if not existing_student and username_check:
            existing_student = Student.objects.filter(
                tenant=tenant,
                deleted_at__isnull=True,
                user__username__endswith=f"_{username_check}",
            ).select_related("user").first()

        if existing_student:
            return Response(
                {
                    "code": "already_registered",
                    "detail": "이미 가입된 아이디입니다.",
                    "student_name": existing_student.name,
                    "student_phone": existing_student.phone or existing_student.parent_phone or "",
                },
                status=409,
            )

        from django.contrib.auth.hashers import make_password
        data = serializer.validated_data.copy()
        password = make_password(data.pop("initial_password"))
        # grade: model은 PositiveSmallIntegerField(null=True) → int 또는 None만 허용
        raw_grade = data.get("grade")
        if raw_grade is not None and raw_grade != "":
            try:
                grade = int(raw_grade)
                if grade < 0 or grade > 32767:
                    grade = None
            except (TypeError, ValueError):
                grade = None
        else:
            grade = None

        try:
            req = StudentRegistrationRequest.objects.create(
                tenant=request.tenant,
                status=StudentRegistrationRequest.PENDING,
                initial_password=password,
                name=data.get("name", ""),
                username=(data.get("username") or "").strip() or "",
                parent_phone=data.get("parent_phone", ""),
                phone=data.get("phone"),
                school_type=data.get("school_type", "HIGH"),
                high_school=(data.get("high_school") or "") or None,
                middle_school=(data.get("middle_school") or "") or None,
                high_school_class=(data.get("high_school_class") or "") or None,
                major=(data.get("major") or "") or None,
                grade=grade,
                gender=(data.get("gender") or "").strip() or None,
                memo=(data.get("memo") or "") or None,
                address=(data.get("address") or "").strip() or None,
                origin_middle_school=(data.get("origin_middle_school") or "").strip() or None,
            )
        except IntegrityError as e:
            logger.warning("RegistrationRequest create IntegrityError: %s", e)
            return Response(
                {"detail": "저장 중 제약 조건 오류가 발생했습니다. 입력값(이름·전화번호 등)을 확인해 주세요.", "error": str(e)},
                status=400,
            )
        except Exception as e:
            logger.exception("RegistrationRequest create error: %s", e)
            return Response(
                {
                    "detail": "가입 신청 저장 중 오류가 발생했습니다.",
                    "error": str(e),
                    "traceback": traceback.format_exc() if settings.DEBUG else None,
                },
                status=500,
            )

        # 자동 승인 설정 시 즉시 승인 처리
        try:
            if getattr(request.tenant, "student_registration_auto_approve", False):
                err = _approve_registration_request(request, req)
                if err is not None:
                    return err
                return Response(
                    StudentDetailSerializer(req.student, context={"request": request}).data,
                    status=200,
                )
        except Exception:
            pass

        try:
            out = RegistrationRequestListSerializer(req, context={"request": request})
            return Response(out.data, status=201)
        except Exception as e:
            logger.exception("RegistrationRequest response serialize error: %s", e)
            return Response(
                {
                    "detail": "가입 신청이 저장되었으나 응답 생성 중 오류가 발생했습니다.",
                    "error": str(e),
                },
                status=500,
            )

    @action(detail=False, methods=["post"], url_path="bulk_approve")
    def bulk_approve(self, request):
        """
        선택한 가입 신청 일괄 승인.
        POST body: { "ids": [1, 2, 3, ...] }
        응답: { "approved": int, "failed": [ {"id": int, "detail": str}, ... ] }
        """
        ids = request.data.get("ids") or []
        if not isinstance(ids, (list, tuple)):
            return Response({"detail": "ids는 배열이어야 합니다."}, status=400)
        ids = [int(x) for x in ids if isinstance(x, (int, str)) and str(x).isdigit()]
        if not ids:
            return Response({"detail": "승인할 ID가 없습니다."}, status=400)

        tenant = request.tenant
        approved_count = 0
        failed = []

        for rid in ids:
            reg = StudentRegistrationRequest.objects.filter(
                tenant=tenant,
                id=rid,
            ).first()
            if not reg:
                failed.append({"id": rid, "detail": "신청을 찾을 수 없습니다."})
                continue
            if reg.status != StudentRegistrationRequest.PENDING:
                failed.append({"id": rid, "detail": "이미 처리된 신청입니다."})
                continue
            err_response = _approve_registration_request(request, reg)
            if err_response is not None:
                failed.append({"id": rid, "detail": err_response.data.get("detail", "승인 실패")})
            else:
                approved_count += 1

        return Response({"approved": approved_count, "failed": failed}, status=200)

    @action(detail=False, methods=["get", "patch"], url_path="settings")
    def registration_settings(self, request):
        """
        가입 신청 설정 조회/수정 (자동 승인).
        GET → { "auto_approve": bool }
        PATCH body: { "auto_approve": bool } → 200 동일 형식
        """
        tenant = request.tenant
        if request.method == "GET":
            try:
                auto_approve = getattr(tenant, "student_registration_auto_approve", False)
            except Exception:
                auto_approve = False
            return Response({
                "auto_approve": bool(auto_approve),
            })
        if request.method == "PATCH":
            auto_approve = request.data.get("auto_approve")
            if auto_approve is not None:
                try:
                    tenant.student_registration_auto_approve = bool(auto_approve)
                    tenant.save(update_fields=["student_registration_auto_approve"])
                except Exception:
                    pass
            try:
                auto_approve = getattr(tenant, "student_registration_auto_approve", False)
            except Exception:
                auto_approve = False
            return Response({
                "auto_approve": bool(auto_approve),
            })
        return Response({"detail": "Method not allowed."}, status=405)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """승인 시 Student + User + TenantMembership 생성 후 status=approved."""
        reg = self.get_object()
        if reg.status != StudentRegistrationRequest.PENDING:
            return Response(
                {"detail": "이미 처리된 신청입니다."},
                status=400,
            )
        err = _approve_registration_request(request, reg)
        if err is not None:
            return err
        out = StudentDetailSerializer(reg.student, context={"request": request})
        return Response(out.data, status=200)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        """가입 신청 거절 → status=rejected."""
        reg = self.get_object()
        if reg.status != StudentRegistrationRequest.PENDING:
            return Response(
                {"detail": "이미 처리된 신청입니다."},
                status=400,
            )
        reg.status = StudentRegistrationRequest.REJECTED
        reg.save(update_fields=["status", "updated_at"])
        return Response({"status": "rejected", "id": reg.id}, status=200)

    @action(detail=False, methods=["post"], url_path="bulk_reject")
    def bulk_reject(self, request):
        """
        선택한 가입 신청 일괄 거절.
        POST body: { "ids": [1, 2, 3, ...] }
        """
        ids = request.data.get("ids") or []
        if not isinstance(ids, (list, tuple)):
            return Response({"detail": "ids는 배열이어야 합니다."}, status=400)
        ids = [int(x) for x in ids if isinstance(x, (int, str)) and str(x).isdigit()]
        if not ids:
            return Response({"detail": "거절할 ID가 없습니다."}, status=400)

        tenant = request.tenant
        updated = StudentRegistrationRequest.objects.filter(
            tenant=tenant,
            id__in=ids,
            status=StudentRegistrationRequest.PENDING,
        ).update(status=StudentRegistrationRequest.REJECTED)
        return Response({"rejected": updated}, status=200)


def _pw_reset_cache_key(tenant_id, phone: str) -> str:
    return f"pw_reset:{tenant_id}:{phone}"


class StudentPasswordFindRequestView(APIView):
    """POST: name, phone → 학생 조회 후 6자리 인증번호 알림톡 발송, 캐시 저장."""
    permission_classes = [AllowAny, TenantResolved]

    def get_authenticators(self):
        return []  # 비로그인 요청 허용, 만료 JWT 시 401 방지

    def post(self, request):
        from django.core.cache import cache
        from apps.support.messaging.selectors import get_auto_send_config
        from apps.support.messaging.policy import MessagingPolicyError, is_messaging_disabled

        name = (request.data.get("name") or "").strip()
        phone = (request.data.get("phone") or "").replace(" ", "").replace("-", "").replace(".", "")
        if not name or not phone or len(phone) != 11 or not phone.startswith("010"):
            return Response(
                {"detail": "이름과 학생 전화번호(010XXXXXXXX 11자리)를 입력해 주세요."},
                status=400,
            )
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant를 확인할 수 없습니다."}, status=400)

        student = Student.objects.filter(
            tenant=tenant,
            deleted_at__isnull=True,
            name=name,
        ).filter(
            Q(phone=phone) | Q(parent_phone=phone)
        ).select_related("user").first()
        if not student or not getattr(student, "user", None):
            return Response(
                {"detail": "해당 이름과 전화번호로 등록된 학생이 없습니다."},
                status=404,
            )
        import random
        code = "".join([str(random.randint(0, 9)) for _ in range(6)])
        key = _pw_reset_cache_key(tenant.id, phone)
        cache.set(key, {"user_id": student.user_id, "code": code}, timeout=600)

        # 알림톡 발송 (AutoSendConfig 템플릿 사용)
        if is_messaging_disabled(tenant.id):
            return Response(
                {"message": "인증번호가 발송되었습니다. (테스트 테넌트에서는 실제 발송이 생략됩니다.)"},
                status=200,
            )

        # 오너 테넌트의 승인된 알림톡 템플릿으로 발송 (모든 테넌트 공통, SMS fallback 없음)
        # password_find_otp 전용 템플릿이 PENDING이면 registration_approved_student로 fallback
        from apps.support.messaging.policy import send_alimtalk_via_owner
        from django.conf import settings as _settings
        site_url = getattr(_settings, "SITE_URL", "") or "https://hakwonplus.com"
        ok = send_alimtalk_via_owner(
            trigger="password_find_otp",
            to=phone,
            replacements={
                "인증번호": code,
                # fallback 시 registration_approved_student 플레이스홀더 매핑
                "학생이름": student.name or "",
                "학생아이디": "인증번호 안내",
                "학생비밀번호": code,
                "사이트링크": site_url,
                "비밀번호안내": "위 인증번호를 10분 내에 입력해 주세요.",
            },
        )

        if not ok:
            return Response(
                {"detail": "인증번호 발송에 실패했습니다. 잠시 후 다시 시도해 주세요."},
                status=503,
            )
        return Response({"message": "인증번호가 발송되었습니다."}, status=200)


class StudentPasswordFindVerifyView(APIView):
    """POST: phone, code, new_password → 인증번호 확인 후 비밀번호 변경."""
    permission_classes = [AllowAny, TenantResolved]

    def get_authenticators(self):
        return []  # 비로그인 요청 허용, 만료 JWT 시 401 방지

    def post(self, request):
        from django.core.cache import cache
        phone = (request.data.get("phone") or "").replace(" ", "").replace("-", "").replace(".", "")
        code = (request.data.get("code") or "").strip()
        new_password = (request.data.get("new_password") or "").strip()
        if not phone or len(phone) != 11 or not code or len(code) != 6 or len(new_password) < 4:
            return Response(
                {"detail": "전화번호, 6자리 인증번호, 새 비밀번호(4자 이상)를 입력해 주세요."},
                status=400,
            )
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant를 확인할 수 없습니다."}, status=400)
        key = _pw_reset_cache_key(tenant.id, phone)
        payload = cache.get(key)
        if not payload or payload.get("code") != code:
            return Response({"detail": "인증번호가 일치하지 않거나 만료되었습니다."}, status=400)
        user_id = payload.get("user_id")
        if not user_id:
            return Response({"detail": "잘못된 요청입니다."}, status=400)
        User = get_user_model()
        user = User.objects.filter(pk=user_id, tenant=tenant).first()
        if not user:
            return Response({"detail": "사용자를 찾을 수 없습니다."}, status=404)
        user.set_password(new_password)
        user.save(update_fields=["password"])
        cache.delete(key)
        return Response({"message": "비밀번호가 변경되었습니다."}, status=200)


def _normalize_phone_for_reset(value):
    """전화번호 정규화 (하이픈 제거, 11자리)."""
    s = (value or "").replace(" ", "").replace("-", "").replace(".", "").strip()
    return s if len(s) == 11 and s.startswith("010") else ""


def _generate_temp_password(length=10):
    """임시 비밀번호 생성 (영문+숫자)."""
    import random
    import string
    chars = string.ascii_letters + string.digits
    return "".join(random.choice(chars) for _ in range(length))


class StudentPasswordResetSendView(APIView):
    """
    POST: 학생 또는 학부모 비밀번호 재설정 — 이름+학생번호 또는 이름+학부모번호로 조회 후
    임시 비밀번호 생성·저장하고 알림톡(SMS)으로 발송.
    """
    permission_classes = [AllowAny, TenantResolved]

    def get_authenticators(self):
        return []

    def post(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant를 확인할 수 없습니다."}, status=400)

        target = (request.data.get("target") or "").strip().lower()
        student_name = (request.data.get("student_name") or "").strip()
        student_ps_number = (request.data.get("student_ps_number") or "").strip()
        parent_phone = _normalize_phone_for_reset(request.data.get("parent_phone") or "")

        if target not in ("student", "parent"):
            return Response({"detail": "대상을 선택해 주세요. (학생 / 학부모)"}, status=400)
        if not student_name:
            return Response({"detail": "학생 이름을 입력해 주세요."}, status=400)

        if target == "student":
            student_phone = _normalize_phone_for_reset(request.data.get("student_phone") or "")
            if not student_ps_number and not student_phone:
                return Response({"detail": "학생 전화번호를 입력해 주세요."}, status=400)
            # 전화번호 우선, 없으면 PS번호로 조회
            if student_phone:
                student = (
                    Student.objects.filter(
                        tenant=tenant,
                        deleted_at__isnull=True,
                        name__iexact=student_name,
                    )
                    .filter(Q(phone=student_phone) | Q(parent_phone=student_phone))
                    .select_related("user")
                    .first()
                )
            else:
                student = (
                    student_repo.student_filter_tenant_ps_number(tenant, student_ps_number)
                    .filter(name__iexact=student_name)
                    .select_related("user")
                    .first()
                )
            if not student or not getattr(student, "user", None):
                return Response(
                    {"detail": "해당 이름과 전화번호로 등록된 학생이 없습니다."},
                    status=404,
                )
            send_to = (student.phone or "").replace(" ", "").replace("-", "").strip()
            if not send_to or len(send_to) != 11:
                send_to = (student.parent_phone or "").replace(" ", "").replace("-", "").strip()
            if not send_to or len(send_to) != 11:
                return Response(
                    {"detail": "등록된 휴대번호가 없어 발송할 수 없습니다. 학원에 문의해 주세요."},
                    status=400,
                )
            user = student.user
            display_name = student.name
            display_username = student.ps_number or user_display_username(user)
        else:
            if not parent_phone:
                return Response({"detail": "학부모 전화번호를 010 11자리로 입력해 주세요."}, status=400)
            student = student_repo.student_filter_tenant_name_parent_phone_active(
                tenant, student_name, parent_phone
            )
            if not student:
                return Response(
                    {"detail": "해당 학생 이름과 학부모 전화번호로 등록된 정보가 없습니다."},
                    status=404,
                )
            from apps.domains.parents.models import Parent
            parent = Parent.objects.filter(tenant=tenant, phone=parent_phone).first()
            if not parent or not getattr(parent, "user", None):
                return Response(
                    {"detail": "학부모 계정을 찾을 수 없습니다. 학원에 문의해 주세요."},
                    status=404,
                )
            user = parent.user
            send_to = parent_phone
            display_name = parent.name or f"{student.name} 학부모"
            display_username = parent_phone

        # ✅ 보안: 클라이언트 지정 비밀번호는 인증된 관리자/교사만 허용
        # (이 엔드포인트는 AllowAny이므로 비인증 요청에서는 서버 생성만 사용)
        client_temp_password = (request.data.get("temp_password") or "").strip()
        is_staff_request = (
            getattr(request, "user", None)
            and request.user.is_authenticated
            and hasattr(request, "tenant")
            and TenantMembership.objects.filter(
                user=request.user, tenant=request.tenant,
                role__in=["owner", "admin", "teacher", "staff"],
                is_active=True,
            ).exists()
        )
        temp_password = (
            client_temp_password
            if client_temp_password and is_staff_request
            else _generate_temp_password()
        )
        old_password_hash = user.password  # 발송 실패 시 롤백용
        user.set_password(temp_password)
        user.save(update_fields=["password"])

        # 알림톡 발송
        from apps.support.messaging.selectors import get_auto_send_config
        from apps.support.messaging.policy import MessagingPolicyError, is_messaging_disabled

        if is_messaging_disabled(tenant.id):
            return Response(
                {"message": "임시 비밀번호가 발송되었습니다. (테스트 환경에서는 실제 발송이 생략됩니다.)"},
                status=200,
            )

        notice = "로그인 후 설정에서 비밀번호를 변경하실 수 있습니다."
        trigger = "password_reset_student" if target == "student" else "password_reset_parent"
        config = get_auto_send_config(tenant.id, trigger)

        if target == "student":
            fallback_text = (
                f"[학원] 비밀번호 찾기\n"
                f"이름: {display_name}\n"
                f"아이디: {display_username}\n"
                f"임시 비밀번호: {temp_password}\n"
                f"{notice}"
            )
        else:
            fallback_text = (
                f"[학원] 비밀번호 찾기\n"
                f"이름: {display_name}\n"
                f"아이디(학부모 전화번호): {display_username}\n"
                f"임시 비밀번호: {temp_password}\n"
                f"{notice}"
            )

        # 오너 테넌트의 승인된 알림톡 템플릿으로 발송 (모든 테넌트 공통, SMS fallback 없음)
        from apps.support.messaging.policy import send_alimtalk_via_owner
        from django.conf import settings as _settings
        site_url = getattr(_settings, "SITE_URL", "") or "https://hakwonplus.com"
        trigger = "password_reset_student" if target == "student" else "password_reset_parent"
        replacements = {
            "학생이름": display_name or "",
            "학생아이디": display_username or "",
            "학생비밀번호": temp_password,
            "사이트링크": site_url,
            "비밀번호안내": notice,
        }
        if target == "parent":
            replacements["학부모아이디"] = display_username or ""
            replacements["학부모비밀번호"] = temp_password
        ok = send_alimtalk_via_owner(trigger=trigger, to=send_to, replacements=replacements)

        if not ok:
            # 발송 실패 시 비밀번호 롤백 — 비밀번호는 바뀌었는데 알림 못 받는 상황 방지
            user.password = old_password_hash
            user.save(update_fields=["password"])
            return Response(
                {"detail": "임시 비밀번호 발송에 실패했습니다. 잠시 후 다시 시도해 주세요."},
                status=503,
            )
        return Response({"message": "임시 비밀번호가 발송되었습니다. 알림톡을 확인해 주세요."}, status=200)


class SendExistingCredentialsView(APIView):
    """
    POST: 이미 등록된 학생에게 기존 아이디 + 임시 비밀번호를 알림톡으로 발송.
    (회원가입 시 중복 감지 → "카카오톡으로 ID/비밀번호 발송" 버튼용)
    """
    permission_classes = [AllowAny, TenantResolved]

    def get_authenticators(self):
        return []

    def post(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant를 확인할 수 없습니다."}, status=400)

        phone = _normalize_phone_for_reset(request.data.get("phone") or "")
        name = (request.data.get("name") or "").strip()

        if not phone or len(phone) != 11:
            return Response({"detail": "전화번호를 입력해 주세요."}, status=400)

        # 학생 조회 (전화번호 또는 학부모전화번호 + 이름)
        qs = Student.objects.filter(tenant=tenant, deleted_at__isnull=True)
        if name:
            qs = qs.filter(name__iexact=name)
        student = qs.filter(
            Q(phone=phone) | Q(parent_phone=phone)
        ).select_related("user").first()

        if not student or not getattr(student, "user", None):
            return Response({"detail": "등록된 학생을 찾을 수 없습니다."}, status=404)

        # 임시 비밀번호 생성 및 저장
        temp_password = _generate_temp_password()
        old_password_hash = student.user.password  # 발송 실패 시 롤백용
        student.user.set_password(temp_password)
        student.user.save(update_fields=["password"])

        display_username = student.ps_number or user_display_username(student.user)
        send_to = (student.phone or "").strip()
        if not send_to or len(send_to) != 11:
            send_to = (student.parent_phone or "").strip()
        if not send_to or len(send_to) != 11:
            return Response({"detail": "등록된 전화번호가 없어 발송할 수 없습니다."}, status=400)

        from apps.support.messaging.policy import MessagingPolicyError, is_messaging_disabled

        if is_messaging_disabled(tenant.id):
            return Response({"message": "아이디/비밀번호가 발송되었습니다."}, status=200)

        # 오너 테넌트의 승인된 알림톡 템플릿으로 발송 (모든 테넌트 공통, SMS fallback 없음)
        from apps.support.messaging.policy import send_alimtalk_via_owner
        from django.conf import settings as _settings
        site_url = getattr(_settings, "SITE_URL", "") or "https://hakwonplus.com"
        ok = send_alimtalk_via_owner(
            trigger="password_reset_student",
            to=send_to,
            replacements={
                "학생이름": student.name or "",
                "학생아이디": display_username or "",
                "학생비밀번호": temp_password,
                "사이트링크": site_url,
                "비밀번호안내": "접속해서 ID\xb7비밀번호를 변경할 수 있습니다.",
            },
        )

        if not ok:
            # 발송 실패 시 비밀번호 롤백
            student.user.password = old_password_hash
            student.user.save(update_fields=["password"])
            return Response({"detail": "발송에 실패했습니다. 잠시 후 다시 시도해 주세요."}, status=503)
        return Response({"message": "아이디와 임시 비밀번호가 알림톡으로 발송되었습니다."}, status=200)
