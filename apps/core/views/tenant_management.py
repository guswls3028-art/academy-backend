# PATH: apps/core/views/tenant_management.py
import logging
import re

from django.db import IntegrityError, transaction

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


class TenantProvisioningConflict(ValueError):
    pass


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
        }
        return Response(data)

    def patch(self, request, tenant_id: int):
        if not is_platform_admin_tenant(request):
            return Response({"detail": "Platform admin tenant required."}, status=403)
        tenant = core_repo.tenant_get_by_id_any(tenant_id)
        if not tenant:
            return Response({"detail": "Tenant not found."}, status=404)

        changes = {}
        if "name" in request.data:
            tenant.name = request.data["name"]
            changes["name"] = tenant.name
        if "isActive" in request.data:
            # parse_bool: "false" 문자열을 False로 처리. bool("false") == True 회귀 방지.
            tenant.is_active = parse_bool(request.data["isActive"], field_name="isActive")
            changes["isActive"] = tenant.is_active
        tenant.save(update_fields=["name", "is_active"])

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
    POST /api/v1/core/tenants/
    플랫폼 관리 테넌트(OWNER_TENANT_ID) 전용 — owner role만. 새 테넌트 생성.
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
                program.plan = Program.Plan.MAX
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
        return Response({
            "id": tenant.id,
            "code": tenant.code,
            "name": tenant.name,
        }, status=201)


class TenantOwnerView(APIView):
    """
    POST /api/v1/core/tenants/<tenant_id>/owner/
    dev_app 전용 — owner role만. 테넌트에 owner 등록.
    User가 없으면 생성 가능 (username, password 필수; name, phone 선택).
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndOwner]

    def post(self, request, tenant_id: int):
        import logging
        logger = logging.getLogger(__name__)
        if not is_platform_admin_tenant(request):
            return Response({"detail": "Platform admin tenant required."}, status=403)
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
                # Tenant row is the owner-set mutex (add/remove use the same lock).
                tenant = Tenant.objects.select_for_update().get(pk=tenant.pk)
                user = core_repo.user_get_by_tenant_username(tenant, username)

                if user:
                    if password:
                        from apps.core.services.password import force_reset_password
                        force_reset_password(user, password)
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
                payload={"username": username, "password": password, "name": name, "phone": phone},
            )
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

    def _get_owner_membership(self, request, tenant_id: int, user_id: int):
        if not is_platform_admin_tenant(request):
            return None, None, 403
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
        tenant, membership, err = self._get_owner_membership(request, tenant_id, user_id)
        if err:
            msg = "Platform admin tenant required." if err == 403 else "Owner not found."
            return Response({"detail": msg}, status=err)
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
        tenant, membership, err = self._get_owner_membership(request, tenant_id, user_id)
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
            active_owner_count = TenantMembership.objects.filter(
                tenant=tenant,
                role="owner",
                is_active=True,
                user__is_active=True,
            ).count()
            if active_owner_count <= 1:
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
