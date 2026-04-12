# PATH: apps/domains/students/views/student_views.py

import logging
import traceback
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

from apps.core.permissions import IsStudent, TenantResolvedAndStaff
from apps.core.models import TenantMembership
from apps.core.models.user import user_display_username

from apps.domains.parents.services import ensure_parent_for_student
from apps.support.messaging.services import send_welcome_messages, get_tenant_site_url, send_event_notification
from apps.domains.ai.gateway import dispatch_job
from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_excel

from academy.adapters.db.django import repositories_students as student_repo
from ..models import Student, StudentRegistrationRequest
from ..filters import StudentFilter
from ..services import normalize_school_from_name
from apps.domains.enrollment.models import Enrollment
from apps.domains.clinic.models import SessionParticipant
from ..serializers import (
    _generate_unique_ps_number,
    StudentListSerializer,
    StudentDetailSerializer,
    AddTagSerializer,
    StudentBulkCreateSerializer,
)

logger = logging.getLogger(__name__)


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
            from ..serializers import StudentCreateSerializer
            return StudentCreateSerializer

        if self.action == "list":
            return StudentListSerializer

        if self.action in ("update", "partial_update"):
            from ..serializers import StudentUpdateSerializer
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
            site_url = get_tenant_site_url(request.tenant)
            send_welcome_messages(
                created_students=[student],
                student_password=password,
                parent_password_by_phone={parent_phone: "0000"} if parent_phone else {},
                site_url=site_url,
            )

        output = StudentDetailSerializer(
            student,
            context={"request": request},
        )
        return Response(output.data, status=201)

    # ------------------------------
    # UPDATE: parent_phone 변경 시 parent FK 동기화
    # ------------------------------
    @transaction.atomic
    def perform_update(self, serializer):
        old_parent_phone = serializer.instance.parent_phone
        instance = serializer.save()

        # parent_phone이 변경되었으면 Parent FK 재연결
        new_parent_phone = serializer.validated_data.get("parent_phone")
        if new_parent_phone and new_parent_phone != old_parent_phone:
            parent = ensure_parent_for_student(
                tenant=self.request.tenant,
                parent_phone=new_parent_phone,
                student_name=instance.name,
            )
            if parent and instance.parent_id != parent.id:
                instance.parent = parent
                instance.save(update_fields=["parent_id", "updated_at"])

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
        # ✅ 소프트 삭제 시 활성 클리닉 예약도 취소 (정원 즉시 반환)
        SessionParticipant.objects.filter(
            student=student, tenant=request.tenant,
            status__in=[SessionParticipant.Status.PENDING, SessionParticipant.Status.BOOKED],
        ).update(status=SessionParticipant.Status.CANCELLED, status_changed_at=now)
        # 퇴원 알림 발송 (학부모)
        _student = student  # closure 캡처용
        _tenant = request.tenant
        _student_id = student.id
        transaction.on_commit(lambda: send_event_notification(
            tenant=_tenant, trigger="withdrawal_complete",
            student=_student, send_to="parent",
            context={
                "강의명": "-",
                "차시명": "-",
                "_domain_object_id": f"withdrawal_{_student_id}",
            },
        ))
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

        # 🔐 tenant-scoped tag lookup: 다른 테넌트 태그 연결 방지
        tag = student_repo.tag_get(serializer.validated_data["tag_id"], tenant=request.tenant)
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

        if len(students_data) > 200:
            return Response(
                {"detail": "최대 200건까지 일괄 처리할 수 있습니다."},
                status=400,
            )
        send_welcome = serializer.validated_data.get("send_welcome_message", False)
        User = get_user_model()
        tenant = request.tenant

        created_count = 0
        failed = []
        created_students = []

        # school_level_mode 검증 준비
        from apps.domains.students.services.school import get_valid_school_types, is_valid_grade
        from apps.core.models import Program
        program = Program.objects.filter(tenant=tenant).first()
        slm = program.feature_flags.get("school_level_mode") if program and program.feature_flags else None
        valid_types = get_valid_school_types(slm)

        for idx, item in enumerate(students_data):
            # school_level_mode 검증
            st_type = item.get("school_type", "HIGH")
            if st_type not in valid_types:
                labels = {"ELEMENTARY": "초등", "MIDDLE": "중등", "HIGH": "고등"}
                allowed = ", ".join(labels.get(t, t) for t in sorted(valid_types))
                failed.append({
                    "row": idx + 1,
                    "name": item.get("name", ""),
                    "error": f"이 학원에서는 {allowed} 학생만 등록할 수 있습니다.",
                })
                continue
            st_grade = item.get("grade")
            if st_grade is not None and not is_valid_grade(st_type, st_grade):
                from apps.domains.students.services.school import GRADE_RANGE
                lo, hi = GRADE_RANGE.get(st_type, (1, 3))
                failed.append({
                    "row": idx + 1,
                    "name": item.get("name", ""),
                    "error": f"{st_type} 학생의 학년은 {lo}~{hi}학년이어야 합니다.",
                })
                continue

            phone = item.get("phone")  # nullable
            parent_phone = item.get("parent_phone", "")
            # ps_number: 임의 6자리 자동 부여 (학생이 추후 변경 가능)
            ps_number = _generate_unique_ps_number(tenant=tenant)
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
                    st, elementary_school, high_school, middle_school = normalize_school_from_name(
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
                        elementary_school=elementary_school,
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
            site_url = get_tenant_site_url(request.tenant)
            parent_pw = {s.parent_phone: "0000" for s in created_students if getattr(s, "parent_phone", None)}
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
                        st, elementary_school, high_school, middle_school = normalize_school_from_name(
                            school_val, student_data.get("school_type")
                        )
                        student.school_type = st
                        student.elementary_school = elementary_school
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
                        student_repo.enrollment_filter_student_delete(student.id, tenant=tenant)
                        user = student.user if student.user_id else None
                        student.delete()
                        if user:
                            # 안전한 패턴: User를 바로 delete()하면 다른 테넌트 데이터까지 cascade 삭제됨
                            # 1) 비활성화 먼저
                            user.is_active = False
                            user.save(update_fields=["is_active"])
                            # 2) 해당 테넌트 멤버십만 삭제
                            TenantMembership.objects.filter(user=user, tenant=tenant).delete()
                            # 3) 다른 테넌트에 멤버십이 없는 고아 User만 삭제
                            if not TenantMembership.objects.filter(user=user).exists():
                                user.delete()
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
                        ps_number = _generate_unique_ps_number(tenant=tenant)
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
                        st, elementary_school, high_school, middle_school = normalize_school_from_name(
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
                            elementary_school=elementary_school,
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
            site_url = get_tenant_site_url(request.tenant)
            parent_pw = {s.parent_phone: "0000" for s in created_students if getattr(s, "parent_phone", None)}
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
        if len(ids) > 200:
            return Response({"detail": "최대 200건까지 일괄 처리할 수 있습니다."}, status=400)
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
                # ✅ 소프트 삭제 시 활성 클리닉 예약도 취소 (정원 즉시 반환)
                SessionParticipant.objects.filter(
                    student=student, tenant=tenant,
                    status__in=[SessionParticipant.Status.PENDING, SessionParticipant.Status.BOOKED],
                ).update(status=SessionParticipant.Status.CANCELLED, status_changed_at=now)
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
        restored = []
        skipped = []
        with transaction.atomic():
            for student in to_restore:
                student.deleted_at = None
                update_fields = ["deleted_at"]
                # ps_number 복원 + 충돌 검사
                if student.ps_number and student.ps_number.startswith("_del_"):
                    parts = student.ps_number.split("_", 3)
                    if len(parts) >= 4:
                        original_ps = parts[3]
                        # 충돌 검사: 다른 활성 학생이 이미 사용 중이면 스킵
                        if Student.objects.filter(
                            tenant=tenant, ps_number=original_ps, deleted_at__isnull=True
                        ).exists():
                            skipped.append({"id": student.id, "reason": f"ps_number '{original_ps}' already in use"})
                            continue
                        student.ps_number = original_ps
                        update_fields.append("ps_number")
                student.save(update_fields=update_fields)
                # User 계정 복원: is_active + phone
                if student.user:
                    user_update = ["is_active"]
                    student.user.is_active = True
                    # phone 복원 (삭제 시 User.phone이 None으로 클리어됨)
                    if not student.user.phone and student.phone:
                        student.user.phone = student.phone
                        user_update.append("phone")
                    student.user.save(update_fields=user_update)
                    TenantMembership.ensure_active(
                        tenant=tenant, user=student.user, role="student"
                    )
                # 학부모 재연결 (삭제 시 parent_id가 None으로 해제됨)
                if not student.parent_id and student.parent_phone:
                    try:
                        parent = ensure_parent_for_student(
                            tenant=tenant,
                            parent_phone=student.parent_phone,
                            student_name=student.name,
                        )
                        if parent:
                            student.parent = parent
                            student.save(update_fields=["parent"])
                    except Exception:
                        logger.warning("bulk_restore: failed to re-link parent for student_id=%s", student.id)
                restored.append(student.id)
                # 복원 시 이전 수강등록은 재활성화하지 않음 (유령 복원 방지)
        result = {"restored": len(restored)}
        if skipped:
            result["skipped"] = skipped
        return Response(result, status=200)

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
                        "SELECT id FROM enrollment_enrollment WHERE student_id IN %s AND tenant_id = %s",
                        [tuple(student_ids), tenant.id],
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
                        "DELETE FROM enrollment_enrollment WHERE student_id IN %s AND tenant_id = %s",
                        [tuple(student_ids), tenant.id],
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
                        tenant_id = tenant.id
                        # submissions_submission.user_id → accounts_user. enrollment 외 제출도 있을 수 있으므로 user_id 기준 정리.
                        # ⚠️ 반드시 tenant_id 필터 포함 — User는 여러 Tenant에 소속 가능
                        cursor.execute(
                            "SELECT 1 FROM information_schema.tables "
                            "WHERE table_schema = %s AND table_name = %s",
                            ["public", "submissions_submission"],
                        )
                        if cursor.fetchone():
                            sub_ids_sql = "SELECT id FROM submissions_submission WHERE user_id IN %s AND tenant_id = %s"
                            cursor.execute(
                                "SELECT 1 FROM information_schema.tables "
                                "WHERE table_schema = %s AND table_name = %s",
                                ["public", "results_exam_result"],
                            )
                            if cursor.fetchone():
                                logger.info("bulk_permanent_delete DELETE results_exam_result (by user submissions)")
                                cursor.execute(
                                    f"DELETE FROM results_exam_result WHERE submission_id IN ({sub_ids_sql})",
                                    [tuple(user_ids), tenant_id],
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
                                    [tuple(user_ids), tenant_id],
                                )
                            logger.info("bulk_permanent_delete DELETE submissions_submission (by user_id, tenant)")
                            cursor.execute(
                                "DELETE FROM submissions_submission WHERE user_id IN %s AND tenant_id = %s",
                                [tuple(user_ids), tenant_id],
                            )
                        # 이 테넌트의 멤버십만 삭제
                        logger.info("bulk_permanent_delete DELETE core_tenantmembership (tenant=%s)", tenant_id)
                        cursor.execute(
                            "DELETE FROM core_tenantmembership WHERE user_id IN %s AND tenant_id = %s",
                            [tuple(user_ids), tenant_id],
                        )
                        # User 계정은 다른 테넌트에 멤버십이 없는 경우에만 삭제
                        cursor.execute(
                            "SELECT id FROM accounts_user WHERE id IN %s AND NOT EXISTS ("
                            "  SELECT 1 FROM core_tenantmembership WHERE user_id = accounts_user.id"
                            ")",
                            [tuple(user_ids)],
                        )
                        orphan_user_ids = [row[0] for row in cursor.fetchall()]
                        if orphan_user_ids:
                            logger.info("bulk_permanent_delete DELETE accounts_user (orphaned) ids=%s", orphan_user_ids)
                            cursor.execute(
                                "DELETE FROM accounts_user WHERE id IN %s",
                                [tuple(orphan_user_ids)],
                            )
                        else:
                            logger.info("bulk_permanent_delete SKIP accounts_user delete — users have other tenant memberships")
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
                    tenant.id, g["name"], g["parent_phone"]
                )
                to_remove = list(
                    student_repo.student_filter_dup_to_remove(
                        tenant.id, g["name"], g["parent_phone"], keep.id
                    )
                )
                for s in to_remove:
                    student_repo.enrollment_filter_student_delete_obj(s, tenant=tenant)
                    user = s.user
                    s.delete()
                    if user:
                        # 안전한 패턴: User를 바로 delete()하면 다른 테넌트 데이터까지 cascade 삭제됨
                        # 1) 비활성화 먼저
                        user.is_active = False
                        user.save(update_fields=["is_active"])
                        # 2) 해당 테넌트 멤버십만 삭제
                        TenantMembership.objects.filter(user=user, tenant=tenant).delete()
                        # 3) 다른 테넌트에 멤버십이 없는 고아 User만 삭제
                        if not TenantMembership.objects.filter(user=user).exists():
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
        from ..services.school import ALL_SCHOOL_TYPES, get_valid_grades
        STRING_FIELD_LIMITS = {
            "name": 100, "phone": 20, "parent_phone": 20,
            "gender": 10, "address": 255, "elementary_school": 100,
            "high_school": 100, "middle_school": 100,
            "origin_middle_school": 100,
            "high_school_class": 100, "major": 50, "memo": None,
        }

        if "school_type" in data and data["school_type"] not in ALL_SCHOOL_TYPES:
            return Response(
                {"detail": f"school_type은 {sorted(ALL_SCHOOL_TYPES)} 중 하나여야 합니다."},
                status=400,
            )
        school_type_for_grade = data.get("school_type", student.school_type)
        if "grade" in data and data["grade"] is not None:
            try:
                grade_val = int(data["grade"])
            except (TypeError, ValueError):
                return Response(
                    {"detail": "grade는 정수여야 합니다."}, status=400,
                )
            valid_grades = get_valid_grades(school_type_for_grade)
            if grade_val not in valid_grades:
                return Response(
                    {"detail": f"grade는 {sorted(valid_grades)} 중 하나여야 합니다."},
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

        old_parent_phone = student.parent_phone  # parent FK 동기화용

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
                "school_type", "elementary_school", "high_school", "middle_school",
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
            # omr_code 재계산: phone 또는 parent_phone 변경 시
            if "phone" in data or "parent_phone" in data:
                phone_str = str(student.phone).strip() if student.phone else None
                pp_str = str(student.parent_phone).strip() if student.parent_phone else None
                if phone_str and len(phone_str) >= 8:
                    new_omr = phone_str[-8:]
                elif pp_str and len(pp_str) >= 8:
                    new_omr = pp_str[-8:]
                else:
                    new_omr = student.omr_code  # 기존값 유지
                if new_omr != student.omr_code:
                    student.omr_code = new_omr
                    if "omr_code" not in update_fields:
                        update_fields.append("omr_code")
            if update_fields:
                student.save(update_fields=update_fields)

            # parent_phone 변경 시 Parent FK 재연결
            if "parent_phone" in data:
                new_parent_phone = str(data["parent_phone"] or "").strip()
                if new_parent_phone and new_parent_phone != (old_parent_phone or ""):
                    parent = ensure_parent_for_student(
                        tenant=tenant,
                        parent_phone=new_parent_phone,
                        student_name=student.name,
                    )
                    if parent and student.parent_id != parent.id:
                        student.parent = parent
                        student.save(update_fields=["parent_id", "updated_at"])

        serializer = StudentDetailSerializer(
            student,
            context={"request": request},
        )
        return Response(serializer.data)
