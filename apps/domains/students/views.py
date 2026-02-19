# PATH: apps/domains/students/views.py

import uuid

from django.db import transaction, connection
from django.utils import timezone
from django.conf import settings
from django.contrib.auth import get_user_model

from rest_framework.viewsets import ModelViewSet
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.pagination import PageNumberPagination
from rest_framework.exceptions import NotFound, ValidationError

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter

from apps.core.permissions import IsAdminOrStaff, IsStudent
from apps.core.models import TenantMembership
from apps.core.permissions import TenantResolvedAndStaff

from apps.domains.parents.services import ensure_parent_for_student
from apps.support.messaging.services import send_welcome_messages, get_site_url
from apps.domains.ai.gateway import dispatch_job
from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_excel

from academy.adapters.db.django import repositories_students as student_repo
from .models import Student, Tag, StudentTag
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
)


# ======================================================
# Tag
# ======================================================

class TagViewSet(ModelViewSet):
    """
    학생 태그 관리
    - 관리자 / 스태프 전용
    - Tag 자체는 테넌트에 종속되지 않음 (공통 분류)
    """
    serializer_class = TagSerializer
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get_queryset(self):
        return student_repo.tag_all()


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
            from .serializers import StudentDetailSerializer
            return Response(
                {
                    "code": "deleted_student_exists",
                    "detail": "삭제된 학생이 있습니다. 복원하시겠습니까?",
                    "deleted_student": StudentDetailSerializer(deleted_student, context={"request": request}).data,
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
                parent_password=password,
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
        student.save(update_fields=update_fields)
        if student.user:
            student.user.is_active = False
            user_update = ["is_active"]
            if student.user.phone:
                student.user.phone = None
                user_update.append("phone")
            student.user.save(update_fields=user_update)
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
    search_fields = ["ps_number", "omr_code", "name", "high_school", "major", "phone"]
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
                            parent_password=password,
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
                            parent_password=password,
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
                student.save(update_fields=update_fields)
                if student.user:
                    student.user.is_active = False
                    user_update = ["is_active"]
                    if student.user.phone:
                        student.user.phone = None
                        user_update.append("phone")
                    student.user.save(update_fields=user_update)
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
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    # Enrollment를 참조하는 테이블들을 먼저 삭제 (존재하는 테이블만)
                    sub = "SELECT id FROM enrollment_enrollment WHERE student_id IN %s"
                    enrollment_child_tables = [
                        "attendance_attendance",
                        "enrollment_sessionenrollment",
                        "video_videopermission",
                        "video_videoprogress",
                        "video_playbacksession",
                        "video_videoplaybackevent",
                    ]
                    params = [tuple(student_ids)]
                    for tbl in enrollment_child_tables:
                        cursor.execute(
                            "SELECT 1 FROM information_schema.tables "
                            "WHERE table_schema = %s AND table_name = %s",
                            ["public", tbl],
                        )
                        if cursor.fetchone():
                            cursor.execute(
                                f"DELETE FROM {tbl} WHERE enrollment_id IN ({sub})",
                                params,
                            )
                    cursor.execute(
                        "DELETE FROM enrollment_enrollment WHERE student_id IN %s",
                        [tuple(student_ids)],
                    )
                    cursor.execute(
                        "DELETE FROM students_studenttag WHERE student_id IN %s",
                        [tuple(student_ids)],
                    )
                    cursor.execute(
                        "DELETE FROM students_student WHERE id IN %s",
                        [tuple(student_ids)],
                    )
                    if user_ids:
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
            import logging
            logging.getLogger(__name__).exception("bulk_permanent_delete failed")
            return Response(
                {"detail": f"영구 삭제 중 오류: {str(e)}"},
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
        methods=["get"],
        url_path="me",
        permission_classes=[IsAuthenticated, IsStudent],
    )
    def me(self, request):
        """
        학생 본인 정보 조회 (Anchor API)

        🔒 보안 포인트
        - request.user + request.tenant 기준 강제
        - 다른 학원 / 다른 학생 접근 불가
        """
        student = student_repo.student_get_tenant_user(request.tenant, request.user)

        serializer = StudentDetailSerializer(
            student,
            context={"request": request},
        )
        return Response(serializer.data)
