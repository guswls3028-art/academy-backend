# PATH: apps/core/views/tenant_management.py
import logging
from django.db import transaction

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.models import Program, Tenant, TenantDomain, TenantMembership
from apps.core.permissions import (
    TenantResolvedAndOwner,
    is_platform_admin_tenant,
)
from apps.core.services.ops_audit import record_audit
from academy.adapters.db.django import repositories_core as core_repo

logger = logging.getLogger(__name__)


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
            tenant.is_active = bool(request.data["isActive"])
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
                "plan": Program.Plan.MAX,
                "feature_flags": {
                    "student_app_enabled": True,
                    "admin_enabled": True,
                },
                "ui_config": {"login_title": name},
                "is_active": True,
            }
        )

        record_audit(
            request,
            action="tenant.create",
            target_tenant=tenant,
            summary=f"Tenant created: {tenant.code} ({tenant.name})",
            payload={"code": code, "name": name, "domain": domain},
        )
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
        membership.is_active = False
        membership.save(update_fields=["is_active"])
        record_audit(
            request,
            action="owner.remove",
            target_tenant=tenant,
            target_user=membership.user,
            summary=f"Owner removed: {getattr(membership.user, 'username', '')} from {tenant.code}",
        )
        return Response(status=204)
