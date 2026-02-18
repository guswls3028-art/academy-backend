# PATH: apps/core/views.py
from datetime import datetime
from django.db import transaction
from django.db.models import Sum

from rest_framework.views import APIView
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response

from drf_yasg.utils import swagger_auto_schema

from apps.core.models import Attendance, Expense, Program, Tenant, TenantDomain, TenantMembership
from academy.adapters.db.django import repositories_core as core_repo
from apps.core.permissions import IsAdminOrStaff, IsSuperuserOnly
from academy.adapters.db.django import repositories_ai as ai_repo
from apps.core.permissions import (
    TenantResolved,
    TenantResolvedAndMember,
    TenantResolvedAndStaff,
    TenantResolvedAndOwner,
)
from apps.core.serializers import (
    UserSerializer,
    ProfileSerializer,
    AttendanceSerializer,
    ExpenseSerializer,
    ProgramPublicSerializer,
    ProgramUpdateSerializer,
)
from apps.core.services.attendance_policy import calculate_duration_hours, calculate_amount
from apps.core.services.expense_policy import normalize_expense_amount


# --------------------------------------------------
# Auth: /core/me/
# --------------------------------------------------

class MeView(APIView):
    """
    ✅ Core Auth Endpoint (Enterprise Final)

    - 인증 필수
    - tenant 확정 필수
    - TenantMembership 존재 필수
    - tenant 기준 role 을 tenantRole 로 반환
    - 프론트는 이 응답만 신뢰 (SSOT)
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndMember]

    @swagger_auto_schema(auto_schema=None)
    def get(self, request):
        serializer = UserSerializer(
            request.user,
            context={"request": request},  # ✅ 핵심
        )
        return Response(serializer.data)


# --------------------------------------------------
# Program: /core/program/
# --------------------------------------------------

class ProgramView(APIView):
    """
    ✅ Program SSOT Endpoint (Enterprise)

    GET  /api/v1/core/program/
      - 로그인 전 AllowAny
      - tenant resolve 필수
      - DB write 발생 금지 (read-only 보장)

    PATCH /api/v1/core/program/
      - Staff only
      - tenant resolve 필수
      - 해당 tenant의 Program만 수정 가능 (1:1)
    """

    @swagger_auto_schema(auto_schema=None)
    def get(self, request):
        tenant = getattr(request, "tenant", None)
        if tenant is None:
            return Response({"detail": "tenant must be resolved"}, status=400)

        program = core_repo.program_get_by_tenant(tenant)
        if program is None:
            # 운영에서는 Tenant 생성 시 signal으로 Program 생성. 없으면 404 (프론트에서 처리)
            return Response(
                {
                    "detail": "program not initialized for tenant",
                    "code": "program_missing",
                    "tenant": tenant.code,
                },
                status=404,
            )

        data = ProgramPublicSerializer(program).data
        return Response(data)

    @swagger_auto_schema(auto_schema=None)
    def patch(self, request):
        tenant = getattr(request, "tenant", None)
        if tenant is None:
            return Response({"detail": "tenant must be resolved"}, status=400)

        program = core_repo.program_get_by_tenant(tenant)
        if program is None:
            return Response(
                {
                    "detail": "program not initialized for tenant",
                    "code": "program_missing",
                    "tenant": tenant.code,
                },
                status=404,
            )

        serializer = ProgramUpdateSerializer(
            program,
            data=(request.data if isinstance(request.data, dict) else {}),
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(ProgramPublicSerializer(program).data)

    def get_permissions(self):
        if self.request.method == "GET":
            return [AllowAny(), TenantResolved()]
        return [IsAuthenticated(), TenantResolvedAndStaff()]


# --------------------------------------------------
# Profile (Staff 영역)
# --------------------------------------------------

class ProfileViewSet(viewsets.ViewSet):
    """
    직원/강사/관리자 전용 Profile API
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @swagger_auto_schema(auto_schema=None)
    @action(detail=False, methods=["get"])
    def me(self, request):
        serializer = ProfileSerializer(request.user)
        return Response(serializer.data)

    @swagger_auto_schema(auto_schema=None)
    @action(detail=False, methods=["patch"])
    def update_me(self, request):
        serializer = ProfileSerializer(
            request.user,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @swagger_auto_schema(auto_schema=None)
    @action(detail=False, methods=["post"], url_path="change-password")
    def change_password(self, request):
        old_pw = request.data.get("old_password")
        new_pw = request.data.get("new_password")

        if not old_pw or not new_pw:
            return Response({"error": "old_password, new_password 필요"}, status=400)

        if not request.user.check_password(old_pw):
            return Response({"error": "현재 비밀번호가 올바르지 않습니다."}, status=400)

        request.user.set_password(new_pw)
        request.user.save()

        return Response({"message": "비밀번호 변경 완료"})


# --------------------------------------------------
# Attendance (Staff 전용)
# --------------------------------------------------

class MyAttendanceViewSet(viewsets.ModelViewSet):
    """
    직원 근태 관리 (tenant 단위 격리)
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    serializer_class = AttendanceSerializer

    def get_queryset(self):
        user = self.request.user
        tenant = getattr(self.request, "tenant", None)
        month = self.request.query_params.get("month")

        qs = core_repo.attendance_filter(user=user, tenant=tenant, month=month)
        return qs

    def perform_create(self, serializer):
        user = self.request.user
        tenant = getattr(self.request, "tenant", None)

        start = self.request.data.get("start_time")
        end = self.request.data.get("end_time")

        duration = calculate_duration_hours(start, end)
        amount = calculate_amount(tenant, duration) if tenant is not None else 0

        serializer.save(
            tenant=tenant,
            user=user,
            duration_hours=duration,
            amount=amount,
        )

    @swagger_auto_schema(auto_schema=None)
    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        user = request.user
        tenant = getattr(self.request, "tenant", None)
        month = self.request.query_params.get("month")

        qs = core_repo.attendance_filter(user=user, tenant=tenant, month=month)
        total_hours = qs.aggregate(Sum("duration_hours"))["duration_hours__sum"] or 0
        total_amount = qs.aggregate(Sum("amount"))["amount__sum"] or 0
        after_tax = int(total_amount * 0.967)

        return Response(
            {
                "total_hours": total_hours,
                "total_amount": total_amount,
                "total_after_tax": after_tax,
            }
        )


# --------------------------------------------------
# Expense (Staff 전용)
# --------------------------------------------------

class MyExpenseViewSet(viewsets.ModelViewSet):
    """
    직원 지출 관리 (tenant 단위 격리)
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    serializer_class = ExpenseSerializer

    def get_queryset(self):
        user = self.request.user
        tenant = getattr(self.request, "tenant", None)
        month = self.request.query_params.get("month")

        qs = core_repo.expense_filter(user=user, tenant=tenant, month=month)
        return qs

    def perform_create(self, serializer):
        tenant = getattr(self.request, "tenant", None)
        raw_amount = self.request.data.get("amount")
        serializer.save(
            tenant=tenant,
            user=self.request.user,
            amount=normalize_expense_amount(raw_amount),
        )


# --------------------------------------------------
# Worker job progress (Redis) — 우하단 실시간 프로그래스바용
# --------------------------------------------------


class JobProgressView(APIView):
    """
    GET /api/v1/core/job_progress/<job_id>/
    Redis에 기록된 워커 진행률 조회. tenant 소속 job만 허용.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, job_id: str):
        from src.infrastructure.cache.redis_progress_adapter import RedisProgressAdapter

        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant가 필요합니다."}, status=400)
        job = ai_repo.get_job_model_for_status(job_id, str(tenant.id))
        if not job:
            return Response({"detail": "해당 작업을 찾을 수 없습니다."}, status=404)
        # ✅ tenant_id 전달 필수 (tenant namespace 키 사용)
        progress = RedisProgressAdapter().get_progress(job_id, tenant_id=str(tenant.id))
        if not progress:
            return Response({"step": None, "percent": None})
        return Response({
            "step": progress.get("step"),
            "percent": progress.get("percent"),
            **{k: v for k, v in progress.items() if k not in ("step", "percent")},
        })


# --------------------------------------------------
# Tenant Branding (admin_app) — 테넌트별 로고·로그인 타이틀, R2 academy-admin
# --------------------------------------------------


def _tenant_branding_dto(program):
    """Program.ui_config → TenantBrandingDto 형태."""
    cfg = getattr(program, "ui_config", None) or {}
    return {
        "tenantId": program.tenant_id,
        "loginTitle": cfg.get("login_title") or "",
        "loginSubtitle": cfg.get("login_subtitle") or "",
        "logoUrl": cfg.get("logo_url") or None,
        "windowTitle": cfg.get("window_title") or "",
        "displayName": program.display_name,
    }


class TenantBrandingView(APIView):
    """
    GET/PATCH /api/v1/core/tenant-branding/<tenant_id>/
    admin_app 전용 — owner role만. Program.ui_config 기반.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndOwner]

    def get(self, request, tenant_id: int):
        tenant = core_repo.tenant_get_by_id_any(tenant_id)
        if not tenant:
            return Response({"detail": "Tenant not found."}, status=404)
        program = core_repo.program_get_by_tenant(tenant)
        if not program:
            return Response({"detail": "Program not found for tenant."}, status=404)
        return Response(_tenant_branding_dto(program))

    def patch(self, request, tenant_id: int):
        tenant = core_repo.tenant_get_by_id_any(tenant_id)
        if not tenant:
            return Response({"detail": "Tenant not found."}, status=404)
        program = core_repo.program_get_by_tenant(tenant)
        if not program:
            return Response({"detail": "Program not found for tenant."}, status=404)
        cfg = dict(program.ui_config or {})
        if "loginTitle" in request.data:
            cfg["login_title"] = request.data.get("loginTitle")
        if "loginSubtitle" in request.data:
            cfg["login_subtitle"] = request.data.get("loginSubtitle")
        if "logoUrl" in request.data:
            cfg["logo_url"] = request.data.get("logoUrl") or None
        if "windowTitle" in request.data:
            cfg["window_title"] = request.data.get("windowTitle") or None
        if "displayName" in request.data:
            program.display_name = request.data.get("displayName")
            program.save(update_fields=["display_name"])
        program.ui_config = cfg
        program.save(update_fields=["ui_config"])
        return Response(_tenant_branding_dto(program))


class TenantBrandingUploadLogoView(APIView):
    """
    POST /api/v1/core/tenant-branding/<tenant_id>/upload-logo/
    multipart/form-data file → R2 academy-admin, Program.ui_config.logo_url 저장.
    admin_app 전용 — owner role만.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndOwner]

    def post(self, request, tenant_id: int):
        tenant = core_repo.tenant_get_by_id_any(tenant_id)
        if not tenant:
            return Response({"detail": "Tenant not found."}, status=404)
        program = core_repo.program_get_by_tenant(tenant)
        if not program:
            return Response({"detail": "Program not found for tenant."}, status=404)

        file = request.FILES.get("file")
        if not file:
            return Response({"detail": "file is required."}, status=400)
        if not (getattr(file, "content_type", "") or "").startswith("image/"):
            return Response({"detail": "Image file required."}, status=400)

        ext = (file.name or "").split(".")[-1].lower() or "png"
        if ext not in ("png", "jpg", "jpeg", "gif", "webp", "svg"):
            ext = "png"
        key = f"tenant-logos/{tenant_id}/logo.{ext}"

        from apps.infrastructure.storage import r2 as r2_storage
        r2_storage.upload_fileobj_to_r2_admin(
            fileobj=file,
            key=key,
            content_type=file.content_type or "image/png",
        )

        logo_url = r2_storage.get_admin_object_public_url(key=key)
        if not logo_url:
            logo_url = r2_storage.generate_presigned_get_url_admin(key=key, expires_in=86400 * 7)

        cfg = dict(program.ui_config or {})
        cfg["logo_url"] = logo_url
        program.ui_config = cfg
        program.save(update_fields=["ui_config"])

        return Response({"logoUrl": logo_url})


# --------------------------------------------------
# Tenant Management: /core/tenants/
# --------------------------------------------------

class TenantListView(APIView):
    """
    GET /api/v1/core/tenants/
    admin_app 전용 — owner role만. 모든 테넌트 목록.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndOwner]

    def get(self, request):
        tenants = Tenant.objects.all().order_by('id')
        data = []
        for tenant in tenants:
            domains = TenantDomain.objects.filter(tenant=tenant, is_active=True)
            primary_domain = domains.filter(is_primary=True).first()
            data.append({
                "id": tenant.id,
                "code": tenant.code,
                "name": tenant.name,
                "isActive": tenant.is_active,
                "primaryDomain": primary_domain.host if primary_domain else None,
                "domains": [d.host for d in domains],
            })
        return Response(data)


class TenantDetailView(APIView):
    """
    GET/PATCH /api/v1/core/tenants/<tenant_id>/
    admin_app 전용 — owner role만. 테넌트 상세 정보.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndOwner]

    def get(self, request, tenant_id: int):
        tenant = core_repo.tenant_get_by_id_any(tenant_id)
        if not tenant:
            return Response({"detail": "Tenant not found."}, status=404)
        
        domains = TenantDomain.objects.filter(tenant=tenant, is_active=True)
        primary_domain = domains.filter(is_primary=True).first()
        program = core_repo.program_get_by_tenant(tenant)
        
        data = {
            "id": tenant.id,
            "code": tenant.code,
            "name": tenant.name,
            "isActive": tenant.is_active,
            "primaryDomain": primary_domain.host if primary_domain else None,
            "domains": [{"host": d.host, "isPrimary": d.is_primary} for d in domains],
            "hasProgram": program is not None,
        }
        return Response(data)

    def patch(self, request, tenant_id: int):
        tenant = core_repo.tenant_get_by_id_any(tenant_id)
        if not tenant:
            return Response({"detail": "Tenant not found."}, status=404)
        
        if "name" in request.data:
            tenant.name = request.data["name"]
        if "isActive" in request.data:
            tenant.is_active = bool(request.data["isActive"])
        tenant.save(update_fields=["name", "is_active"])
        
        return self.get(request, tenant_id)


class TenantCreateView(APIView):
    """
    POST /api/v1/core/tenants/
    admin_app 전용 — owner role만. 새 테넌트 생성.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndOwner]

    def post(self, request):
        code = request.data.get("code")
        name = request.data.get("name")
        domain = request.data.get("domain")
        
        if not code or not name:
            return Response({"detail": "code and name are required."}, status=400)
        
        # 테넌트 생성
        tenant, created = core_repo.tenant_get_or_create(
            code,
            defaults={"name": name, "is_active": True}
        )
        
        if not created:
            return Response({"detail": f"Tenant with code '{code}' already exists."}, status=400)
        
        # 도메인 설정
        if domain:
            domain_obj, _ = core_repo.tenant_domain_get_or_create_by_defaults(
                domain,
                defaults={
                    "tenant": tenant,
                    "is_primary": True,
                    "is_active": True,
                }
            )
        
        # Program 생성
        program, _ = core_repo.program_get_or_create(
            tenant,
            defaults={
                "display_name": name,
                "brand_key": code,
                "login_variant": Program.LoginVariant.HAKWONPLUS,
                "plan": Program.Plan.PREMIUM,
                "feature_flags": {
                    "student_app_enabled": True,
                    "admin_enabled": True,
                },
                "ui_config": {"login_title": name},
                "is_active": True,
            }
        )
        
        return Response({
            "id": tenant.id,
            "code": tenant.code,
            "name": tenant.name,
        }, status=201)


class TenantOwnerView(APIView):
    """
    POST /api/v1/core/tenants/<tenant_id>/owner/
    admin_app 전용 — owner role만. 테넌트에 owner 등록.
    User가 없으면 생성 가능 (username, password 필수; name, phone 선택).
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndOwner]

    def post(self, request, tenant_id: int):
        import logging
        logger = logging.getLogger(__name__)
        try:
            tenant = core_repo.tenant_get_by_id_any(tenant_id)
            if not tenant:
                return Response({"detail": "Tenant not found."}, status=404)

            username = request.data.get("username")
            password = request.data.get("password")
            name = request.data.get("name")
            phone = request.data.get("phone")

            if not username:
                return Response({"detail": "username is required."}, status=400)

            from django.contrib.auth import get_user_model
            User = get_user_model()

            with transaction.atomic():
                user = core_repo.user_get_by_tenant_username(tenant, username)

                if user:
                    if password:
                        user.set_password(password)
                        user.save(update_fields=["password"])
                    if name is not None:
                        user.name = name
                    if phone is not None:
                        user.phone = phone
                    if name is not None or phone is not None:
                        user.save(update_fields=["name", "phone"])
                else:
                    if not password:
                        return Response(
                            {"detail": "password is required when creating a new user."},
                            status=400,
                        )
                    from apps.core.models.user import user_internal_username
                    user = User.objects.create_user(
                        username=user_internal_username(tenant, username),
                        password=password,
                        tenant=tenant,
                        email="",
                        name=name or "",
                        phone=phone or "",
                    )

                membership = core_repo.membership_ensure_active(
                    tenant=tenant,
                    user=user,
                    role="owner",
                )
                if membership.role != "owner":
                    membership.role = "owner"
                    membership.save(update_fields=["role"])

                # 테넌트 원장명 동기화: 비어 있으면 이 사용자로 설정 (강의 담당자 등에서 참조)
                from apps.core.models.user import user_display_username
                owner_display = (getattr(user, "name", None) or user_display_username(user) or "").strip()
                if owner_display and not (tenant.owner_name or "").strip():
                    tenant.owner_name = owner_display[:100]
                    tenant.save(update_fields=["owner_name"])

            from apps.core.models.user import user_display_username
            return Response({
                "tenantId": tenant.id,
                "tenantCode": tenant.code,
                "userId": user.id,
                "username": user_display_username(user),
                "name": getattr(user, "name", "") or "",
                "role": membership.role,
            })
        except Exception as e:
            logger.exception("TenantOwnerView post failed: %s", e)
            return Response(
                {"detail": str(e)},
                status=500,
            )


class TenantOwnerListView(APIView):
    """
    GET /api/v1/core/tenants/<tenant_id>/owners/
    admin_app 전용 — owner role만. 해당 테넌트의 Owner 목록 조회.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndOwner]

    def get(self, request, tenant_id: int):
        tenant = core_repo.tenant_get_by_id_any(tenant_id)
        if not tenant:
            return Response({"detail": "Tenant not found."}, status=404)
        memberships = (
            TenantMembership.objects.filter(
                tenant=tenant,
                role="owner",
                is_active=True,
            )
            .select_related("user")
            .order_by("user__username")
        )
        from apps.core.models.user import user_display_username
        data = [
            {
                "userId": m.user_id,
                "username": user_display_username(m.user),
                "name": getattr(m.user, "name", "") or "",
                "phone": getattr(m.user, "phone", "") or "",
                "role": m.role,
            }
            for m in memberships
        ]
        return Response(data)


class TenantOwnerDetailView(APIView):
    """
    PATCH /api/v1/core/tenants/<tenant_id>/owners/<user_id>/
      - owner 사용자 이름/전화번호 수정
    DELETE /api/v1/core/tenants/<tenant_id>/owners/<user_id>/
      - 해당 테넌트에서 owner 제거 (TenantMembership is_active=False)
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndOwner]

    def _get_owner_membership(self, tenant_id: int, user_id: int):
        tenant = core_repo.tenant_get_by_id_any(tenant_id)
        if not tenant:
            return None, None, 404
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = User.objects.filter(id=user_id).first()
        if not user:
            return None, None, 404
        membership = TenantMembership.objects.filter(
            tenant=tenant,
            user=user,
            role="owner",
            is_active=True,
        ).first()
        if not membership:
            return None, None, 404
        return tenant, membership, None

    def patch(self, request, tenant_id: int, user_id: int):
        tenant, membership, err = self._get_owner_membership(tenant_id, user_id)
        if err:
            return Response({"detail": "Owner not found."}, status=err)
        user = membership.user
        if "name" in request.data:
            user.name = request.data.get("name") or ""
        if "phone" in request.data:
            user.phone = request.data.get("phone") or ""
        user.save(update_fields=["name", "phone"])
        from apps.core.models.user import user_display_username
        return Response({
            "userId": user.id,
            "username": user_display_username(user),
            "name": getattr(user, "name", "") or "",
            "role": membership.role,
        })

    def delete(self, request, tenant_id: int, user_id: int):
        tenant, membership, err = self._get_owner_membership(tenant_id, user_id)
        if err:
            return Response({"detail": "Owner not found."}, status=err)
        membership.is_active = False
        membership.save(update_fields=["is_active"])
        return Response(status=204)
