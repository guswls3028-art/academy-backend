# PATH: apps/core/views/tenant_management.py
import logging
import re

from django.conf import settings
from django.db import IntegrityError, transaction

try:
    from drf_spectacular.utils import extend_schema
except ModuleNotFoundError as exc:
    if exc.name != "drf_spectacular":
        raise

    def extend_schema(*args, **kwargs):  # type: ignore[no-redef]
        """Keep runtime views importable when schema-only tooling is absent."""

        def decorator(view):
            return view

        return decorator
from rest_framework import serializers
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.models import Program, Tenant, TenantDomain, TenantMembership
from apps.core.parsing import parse_bool
from apps.core.permissions import (
    TenantResolvedAndOwner,
    is_platform_admin_tenant,
)
from apps.core.services.ops_audit import record_audit
from academy.adapters.db.django import repositories_core as core_repo

logger = logging.getLogger(__name__)


class TenantOwnerRegistrationSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150, trim_whitespace=True)
    password = serializers.CharField(
        required=False,
        allow_blank=False,
        min_length=4,
        max_length=128,
        trim_whitespace=False,
        write_only=True,
    )
    name = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=50,
    )
    phone = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=20,
    )
    promote_existing = serializers.BooleanField(required=False, default=False)

    def validate_username(self, value):
        tenant = self.context["tenant"]
        from django.contrib.auth import get_user_model

        stored_max_length = get_user_model()._meta.get_field("username").max_length
        prefix_length = len(f"t{tenant.id}_")
        if len(value) > stored_max_length - prefix_length:
            raise serializers.ValidationError(
                "테넌트 접두사를 포함한 아이디 길이가 허용 범위를 초과합니다."
            )
        return value


class TenantOwnerUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=50,
    )
    phone = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=20,
    )


class TenantOwnerPasswordResetSerializer(serializers.Serializer):
    password = serializers.CharField(
        min_length=4,
        max_length=128,
        trim_whitespace=False,
        write_only=True,
    )


class TenantOwnerPasswordResetResponseSerializer(serializers.Serializer):
    detail = serializers.CharField()
    userId = serializers.IntegerField()
    mustChangePassword = serializers.BooleanField()


OWNER_HANDOFF_STATUS_CHOICES = (
    "account_inactive",
    "password_setup_required",
    "first_login_pending",
    "complete",
)


def _owner_handoff_status(user) -> str:
    if not user.is_active:
        return "account_inactive"
    if not user.has_usable_password():
        return "password_setup_required"
    if getattr(user, "must_change_password", False):
        return "first_login_pending"
    return "complete"


class TenantOwnerListItemSerializer(serializers.Serializer):
    userId = serializers.IntegerField()
    username = serializers.CharField()
    name = serializers.CharField(allow_blank=True)
    phone = serializers.CharField(allow_blank=True)
    isActive = serializers.BooleanField()
    hasUsablePassword = serializers.BooleanField()
    mustChangePassword = serializers.BooleanField()
    handoffStatus = serializers.ChoiceField(choices=OWNER_HANDOFF_STATUS_CHOICES)
    role = serializers.CharField()


class TenantProvisioningConflict(ValueError):
    pass


def _get_active_owner_membership(request, tenant_id: int, user_id: int):
    if not is_platform_admin_tenant(request):
        return None, None, 403
    tenant = core_repo.tenant_get_by_id_any(tenant_id)
    if not tenant:
        return None, None, 404
    membership = (
        TenantMembership.objects.filter(
            tenant=tenant,
            user_id=user_id,
            role="owner",
            is_active=True,
        )
        .select_related("user")
        .first()
    )
    if not membership:
        return None, None, 404
    return tenant, membership, None


def _normalize_tenant_code(value) -> str | None:
    code = str(value or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,48}[a-z0-9])?", code):
        return None
    return code


def _normalize_tenant_host(value) -> str | None:
    raw = str(value or "").strip().lower().rstrip(".")
    if not raw:
        return ""
    if "://" in raw or "/" in raw or any(char.isspace() for char in raw):
        return None
    if ":" in raw:
        host, separator, port = raw.rpartition(":")
        if (
            not separator
            or not host
            or not port.isdigit()
            or not 1 <= int(port) <= 65535
        ):
            return None
        raw = host
    if len(raw) > 255 or ".." in raw:
        return None
    labels = raw.split(".")
    if any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in labels
    ):
        return None
    return raw


# --------------------------------------------------
# Tenant Management: /core/tenants/
# --------------------------------------------------

class TenantListView(APIView):
    """
    GET /api/v1/core/tenants/
    플랫폼 관리 테넌트(OWNER_TENANT_ID) 전용 — owner role만. 모든 테넌트 목록.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndOwner]

    def get(self, request):
        if not is_platform_admin_tenant(request):
            return Response({"detail": "Platform admin tenant required."}, status=403)
        tenants = Tenant.objects.all().order_by('id')
        # Prefetch programs for feature_flags
        programs_by_tenant = {
            p.tenant_id: p
            for p in Program.objects.filter(tenant__in=tenants)
        }
        data = []
        for tenant in tenants:
            domains = TenantDomain.objects.filter(tenant=tenant, is_active=True)
            primary_domain = domains.filter(is_primary=True).first()
            program = programs_by_tenant.get(tenant.id)
            data.append({
                "id": tenant.id,
                "code": tenant.code,
                "name": tenant.name,
                "isActive": tenant.is_active,
                "primaryDomain": primary_domain.host if primary_domain else None,
                "domains": [d.host for d in domains],
                "featureFlags": program.feature_flags if program else {},
            })
        return Response(data)


class TenantDetailView(APIView):
    """
    GET/PATCH /api/v1/core/tenants/<tenant_id>/
    플랫폼 관리 테넌트(OWNER_TENANT_ID) 전용 — owner role만. 테넌트 상세 정보.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndOwner]

    def get(self, request, tenant_id: int):
        if not is_platform_admin_tenant(request):
            return Response({"detail": "Platform admin tenant required."}, status=403)
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
            "featureFlags": program.feature_flags if program else {},
        }
        return Response(data)

    def patch(self, request, tenant_id: int):
        if not is_platform_admin_tenant(request):
            return Response({"detail": "Platform admin tenant required."}, status=403)
        tenant = core_repo.tenant_get_by_id_any(tenant_id)
        if not tenant:
            return Response({"detail": "Tenant not found."}, status=404)

        changes = {}
        analytics_enabled = None
        analytics_program = None
        if "productUsageAnalyticsEnabled" in request.data:
            analytics_enabled = parse_bool(
                request.data["productUsageAnalyticsEnabled"],
                field_name="productUsageAnalyticsEnabled",
            )
            if (
                analytics_enabled
                and not getattr(settings, "PRODUCT_ANALYTICS_HASH_KEY", "")
            ):
                return Response(
                    {
                        "detail": "PRODUCT_ANALYTICS_HASH_KEY must be configured before rollout.",
                        "code": "analytics_hash_key_missing",
                    },
                    status=409,
                )
            analytics_program = core_repo.program_get_by_tenant(tenant)
            if analytics_program is None:
                return Response(
                    {"detail": "Program not found.", "code": "program_missing"},
                    status=409,
                )

        tenant_update_fields = []
        if "name" in request.data:
            tenant.name = request.data["name"]
            changes["name"] = tenant.name
            tenant_update_fields.append("name")
        if "isActive" in request.data:
            # parse_bool: "false" 문자열을 False로 처리. bool("false") == True 회귀 방지.
            tenant.is_active = parse_bool(request.data["isActive"], field_name="isActive")
            changes["isActive"] = tenant.is_active
            tenant_update_fields.append("is_active")
        if tenant_update_fields:
            tenant.save(update_fields=tenant_update_fields)

        if analytics_enabled is not None and analytics_program is not None:
            next_flags = dict(analytics_program.feature_flags or {})
            next_flags["product_usage_analytics_enabled"] = analytics_enabled
            analytics_program.feature_flags = next_flags
            analytics_program.save(update_fields=["feature_flags"])
            changes["productUsageAnalyticsEnabled"] = analytics_enabled

        if changes:
            record_audit(
                request,
                action="tenant.update",
                target_tenant=tenant,
                summary=f"Tenant updated: {tenant.code} {changes}",
                payload=changes,
            )
        return self.get(request, tenant_id)


class TenantCreateView(APIView):
    """
    POST /api/v1/core/tenants/create/
    플랫폼 관리 테넌트(OWNER_TENANT_ID) 전용 — 개발·QA 기본 테넌트 생성.
    운영 신규 테넌트는 명시적 ID와 전체 설정을 받는 provision_tenant를 사용한다.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndOwner]

    def post(self, request):
        if not is_platform_admin_tenant(request):
            return Response({"detail": "Platform admin tenant required."}, status=403)
        code = _normalize_tenant_code(request.data.get("code"))
        name = str(request.data.get("name") or "").strip()
        domain = _normalize_tenant_host(request.data.get("domain"))

        if not code:
            return Response({"detail": "code_invalid"}, status=400)
        if not name or len(name) > 255:
            return Response({"detail": "name_invalid"}, status=400)
        if domain is None:
            return Response({"detail": "domain_invalid"}, status=400)

        try:
            with transaction.atomic():
                if Tenant.objects.select_for_update().filter(code=code).exists():
                    raise TenantProvisioningConflict("tenant_code_conflict")
                candidate_hosts = {code}
                if domain:
                    candidate_hosts.add(domain)
                conflict = (
                    TenantDomain.objects.select_for_update()
                    .filter(host__in=candidate_hosts)
                    .first()
                )
                if conflict:
                    raise TenantProvisioningConflict("tenant_domain_conflict")

                # The Tenant post-save bootstrap creates Program and a primary
                # code-host row inside this same outer transaction.
                tenant = Tenant.objects.create(code=code, name=name, is_active=True)
                code_domain = TenantDomain.objects.select_for_update().filter(host=code).first()
                if not code_domain or code_domain.tenant_id != tenant.id:
                    raise TenantProvisioningConflict("tenant_domain_conflict")
                if domain and domain != code:
                    code_domain.is_primary = False
                    code_domain.save(update_fields=["is_primary"])
                    TenantDomain.objects.create(
                        tenant=tenant,
                        host=domain,
                        is_primary=True,
                        is_active=True,
                    )

                program, _ = core_repo.program_get_or_create(tenant, defaults={})
                program.display_name = name
                program.brand_key = code
                program.login_variant = Program.LoginVariant.HAKWONPLUS
                program.plan = Program.Plan.ALL
                program.feature_flags = {
                    "student_app_enabled": True,
                    "admin_enabled": True,
                }
                program.ui_config = {"login_title": name}
                program.is_active = True
                program.save(update_fields=[
                    "display_name",
                    "brand_key",
                    "login_variant",
                    "plan",
                    "feature_flags",
                    "ui_config",
                    "is_active",
                ])

                record_audit(
                    request,
                    action="tenant.create",
                    target_tenant=tenant,
                    summary=f"Tenant created: {tenant.code} ({tenant.name})",
                    payload={"code": code, "name": name, "domain": domain},
                )
        except TenantProvisioningConflict as exc:
            return Response({"detail": str(exc)}, status=409)
        except IntegrityError:
            return Response({"detail": "tenant_provisioning_conflict"}, status=409)
        domains = TenantDomain.objects.filter(tenant=tenant, is_active=True)
        primary_domain = domains.filter(is_primary=True).first()
        return Response({
            "id": tenant.id,
            "code": tenant.code,
            "name": tenant.name,
            "isActive": tenant.is_active,
            "primaryDomain": primary_domain.host if primary_domain else None,
            "domains": [domain.host for domain in domains],
        }, status=201)


class TenantOwnerView(APIView):
    """
    POST /api/v1/core/tenants/<tenant_id>/owner/
    dev_app 전용 — owner role만. 테넌트에 owner 등록.
    User가 없으면 생성 가능 (username, password 필수; name, phone 선택).
    기존 User 승격은 promote_existing=true 재확인이 필요하며 자격 증명은 변경하지 않음.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndOwner]

    def post(self, request, tenant_id: int):
        if not is_platform_admin_tenant(request):
            return Response({"detail": "Platform admin tenant required."}, status=403)
        tenant = core_repo.tenant_get_by_id_any(tenant_id)
        if not tenant:
            return Response({"detail": "Tenant not found."}, status=404)

        serializer = TenantOwnerRegistrationSerializer(
            data=request.data,
            context={"tenant": tenant},
        )
        if not serializer.is_valid():
            return Response(
                {
                    "detail": "owner_registration_invalid",
                    "errors": serializer.errors,
                },
                status=400,
            )

        username = serializer.validated_data["username"]
        password = serializer.validated_data.get("password")
        name = serializer.validated_data.get("name")
        phone = serializer.validated_data.get("phone")
        promote_existing = serializer.validated_data["promote_existing"]

        try:
            from django.contrib.auth import get_user_model
            User = get_user_model()

            with transaction.atomic():
                # Tenant row is the owner-set mutex (add/remove use the same lock).
                tenant = Tenant.objects.select_for_update().get(pk=tenant.pk)
                candidates = core_repo.user_list_by_tenant_login_identifier(
                    tenant,
                    username,
                )
                direct_user = core_repo.user_get_by_tenant_username(tenant, username)
                if direct_user and all(
                    candidate.id != direct_user.id for candidate in candidates
                ):
                    candidates.append(direct_user)

                if len(candidates) > 1:
                    record_audit(
                        request,
                        action="owner.register",
                        target_tenant=tenant,
                        summary=f"Owner register rejected: ambiguous identifier in {tenant.code}",
                        payload={
                            "username": username,
                            "reason": "owner_identifier_ambiguous",
                        },
                        result="failed",
                        error="owner_identifier_ambiguous",
                    )
                    return Response(
                        {"detail": "owner_identifier_ambiguous"},
                        status=409,
                    )

                user = candidates[0] if candidates else None
                created_user = user is None

                if user:
                    membership = TenantMembership.objects.filter(
                        tenant=tenant,
                        user=user,
                        is_active=True,
                    ).first()
                    if membership and membership.role == "owner":
                        record_audit(
                            request,
                            action="owner.register",
                            target_tenant=tenant,
                            target_user=user,
                            summary=f"Owner register rejected: {username} already owns {tenant.code}",
                            payload={
                                "username": username,
                                "reason": "owner_already_registered",
                            },
                            result="failed",
                            error="owner_already_registered",
                        )
                        return Response(
                            {"detail": "owner_already_registered"},
                            status=409,
                        )
                    if not user.is_active:
                        record_audit(
                            request,
                            action="owner.register",
                            target_tenant=tenant,
                            target_user=user,
                            summary=f"Owner register rejected: {username} is inactive",
                            payload={
                                "username": username,
                                "reason": "owner_user_inactive",
                            },
                            result="failed",
                            error="owner_user_inactive",
                        )
                        return Response(
                            {"detail": "owner_user_inactive"},
                            status=409,
                        )
                    if not promote_existing:
                        record_audit(
                            request,
                            action="owner.register",
                            target_tenant=tenant,
                            target_user=user,
                            summary=f"Owner promotion requires confirmation: {username} in {tenant.code}",
                            payload={
                                "username": username,
                                "current_role": getattr(membership, "role", ""),
                                "reason": "owner_promotion_confirmation_required",
                            },
                            result="failed",
                            error="owner_promotion_confirmation_required",
                        )
                        return Response(
                            {
                                "detail": "owner_promotion_confirmation_required",
                                "currentRole": getattr(membership, "role", ""),
                            },
                            status=409,
                        )
                else:
                    if promote_existing:
                        return Response(
                            {"detail": "owner_existing_user_not_found"},
                            status=409,
                        )
                    if not password:
                        return Response(
                            {"detail": "owner_password_required"},
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
                        must_change_password=True,
                    )

                membership = core_repo.membership_ensure_active(
                    tenant=tenant,
                    user=user,
                    role="owner",
                )

                # 테넌트 원장명 동기화: 비어 있으면 이 사용자로 설정 (강의 담당자 등에서 참조)
                from apps.core.models.user import user_display_username
                owner_display = (getattr(user, "name", None) or user_display_username(user) or "").strip()
                if owner_display and not (tenant.owner_name or "").strip():
                    tenant.owner_name = owner_display[:100]
                    tenant.save(update_fields=["owner_name"])

            from apps.core.models.user import user_display_username
            record_audit(
                request,
                action="owner.register",
                target_tenant=tenant,
                target_user=user,
                summary=f"Owner registered: {username} -> {tenant.code}",
                payload={
                    "username": username,
                    "created_user": created_user,
                    "promoted_existing": not created_user,
                    "password_changed": created_user,
                },
            )
            return Response({
                "tenantId": tenant.id,
                "tenantCode": tenant.code,
                "userId": user.id,
                "username": user_display_username(user),
                "name": getattr(user, "name", "") or "",
                "isActive": bool(user.is_active),
                "hasUsablePassword": bool(user.has_usable_password()),
                "mustChangePassword": bool(
                    getattr(user, "must_change_password", False)
                ),
                "handoffStatus": _owner_handoff_status(user),
                "role": membership.role,
            })
        except Exception as e:
            logger.exception("TenantOwnerView post failed: %s", e)
            record_audit(
                request,
                action="owner.register",
                summary="Owner register failed",
                payload={"tenant_id": tenant_id},
                result="failed",
                error=str(e)[:200],
            )
            return Response(
                {"detail": "Owner 등록 중 오류가 발생했습니다."},
                status=500,
            )


class TenantOwnerListView(APIView):
    """
    GET /api/v1/core/tenants/<tenant_id>/owners/
    dev_app 전용 — owner role만. 해당 테넌트의 Owner 목록 조회.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndOwner]

    @extend_schema(responses={200: TenantOwnerListItemSerializer(many=True)})
    def get(self, request, tenant_id: int):
        if not is_platform_admin_tenant(request):
            return Response({"detail": "Platform admin tenant required."}, status=403)
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
                "isActive": bool(m.user.is_active),
                "hasUsablePassword": bool(m.user.has_usable_password()),
                "mustChangePassword": bool(
                    getattr(m.user, "must_change_password", False)
                ),
                "handoffStatus": _owner_handoff_status(m.user),
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

    def patch(self, request, tenant_id: int, user_id: int):
        tenant, membership, err = _get_active_owner_membership(
            request,
            tenant_id,
            user_id,
        )
        if err:
            msg = "Platform admin tenant required." if err == 403 else "Owner not found."
            return Response({"detail": msg}, status=err)
        serializer = TenantOwnerUpdateSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(
                {"detail": "owner_update_invalid", "errors": serializer.errors},
                status=400,
            )
        user = membership.user
        changed_fields = []
        for field in ("name", "phone"):
            if field in serializer.validated_data:
                setattr(user, field, serializer.validated_data[field] or "")
                changed_fields.append(field)
        if changed_fields:
            user.save(update_fields=changed_fields)
            record_audit(
                request,
                action="owner.update",
                target_tenant=tenant,
                target_user=user,
                summary=f"Owner profile updated in {tenant.code}",
                payload={"changed_fields": changed_fields},
            )
        from apps.core.models.user import user_display_username
        return Response({
            "userId": user.id,
            "username": user_display_username(user),
            "name": getattr(user, "name", "") or "",
            "phone": getattr(user, "phone", "") or "",
            "isActive": bool(user.is_active),
            "hasUsablePassword": bool(user.has_usable_password()),
            "mustChangePassword": bool(
                getattr(user, "must_change_password", False)
            ),
            "handoffStatus": _owner_handoff_status(user),
            "role": membership.role,
        })

    def delete(self, request, tenant_id: int, user_id: int):
        tenant, membership, err = _get_active_owner_membership(
            request,
            tenant_id,
            user_id,
        )
        if err:
            msg = "Platform admin tenant required." if err == 403 else "Owner not found."
            return Response({"detail": msg}, status=err)
        if int(request.user.id) == int(user_id):
            return Response(
                {"detail": "owner_self_removal_forbidden"},
                status=409,
            )
        with transaction.atomic():
            Tenant.objects.select_for_update().get(pk=tenant.pk)
            from django.contrib.auth import get_user_model
            target_user = get_user_model().objects.select_for_update().get(pk=user_id)
            membership = TenantMembership.objects.select_for_update().get(
                tenant=tenant,
                user=target_user,
                role="owner",
                is_active=True,
            )
            remaining_active_owner_count = TenantMembership.objects.filter(
                tenant=tenant,
                role="owner",
                is_active=True,
                user__is_active=True,
            ).exclude(user=target_user).count()
            if remaining_active_owner_count < 1:
                return Response(
                    {"detail": "final_active_owner_required"},
                    status=409,
                )
            from apps.core.services.tenant_access import deactivate_tenant_membership
            deactivate_tenant_membership(
                user=target_user,
                tenant=tenant,
                allowed_roles=("owner",),
            )
        record_audit(
            request,
            action="owner.remove",
            target_tenant=tenant,
            target_user=membership.user,
            summary=f"Owner removed: {getattr(membership.user, 'username', '')} from {tenant.code}",
        )
        return Response(status=204)


class TenantOwnerPasswordResetView(APIView):
    """
    POST /api/v1/core/tenants/<tenant_id>/owners/<user_id>/password/
      - 플랫폼 운영자가 활성 owner의 임시 비밀번호를 재설정
      - 기존 세션 무효화 및 다음 로그인 비밀번호 변경 강제
    """

    @extend_schema(
        request=TenantOwnerPasswordResetSerializer,
        responses={200: TenantOwnerPasswordResetResponseSerializer},
    )
    def post(self, request, tenant_id: int, user_id: int):
        tenant, _membership, err = _get_active_owner_membership(
            request,
            tenant_id,
            user_id,
        )
        if err:
            msg = "Platform admin tenant required." if err == 403 else "Owner not found."
            return Response({"detail": msg}, status=err)

        serializer = TenantOwnerPasswordResetSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    "detail": "owner_password_reset_invalid",
                    "errors": serializer.errors,
                },
                status=400,
            )

        with transaction.atomic():
            Tenant.objects.select_for_update().get(pk=tenant.pk)
            from django.contrib.auth import get_user_model

            target_user = get_user_model().objects.select_for_update().get(pk=user_id)
            if not target_user.is_active:
                return Response(
                    {"detail": "owner_user_inactive"},
                    status=409,
                )
            try:
                TenantMembership.objects.select_for_update().get(
                    tenant=tenant,
                    user=target_user,
                    role="owner",
                    is_active=True,
                )
            except TenantMembership.DoesNotExist:
                return Response({"detail": "Owner not found."}, status=404)

            from apps.core.services.password import (
                clear_pending_password_reset,
                force_reset_password,
            )

            force_reset_password(
                target_user,
                serializer.validated_data["password"],
            )
            clear_pending_password_reset(target_user)

        record_audit(
            request,
            action="owner.password_reset",
            target_tenant=tenant,
            target_user=target_user,
            summary=f"Owner password reset in {tenant.code}",
            payload={"user_id": target_user.id},
        )
        return Response(
            {
                "detail": "owner_password_reset",
                "userId": target_user.id,
                "mustChangePassword": True,
            }
        )
