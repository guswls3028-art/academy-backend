# PATH: apps/core/views/tenant_branding.py
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.permissions import (
    TenantResolvedAndOwner,
    is_platform_admin_tenant,
)
from academy.adapters.db.django import repositories_core as core_repo


# --------------------------------------------------
# Tenant Branding (dev_app) — 테넌트별 로고·로그인 타이틀, R2 academy-admin
# --------------------------------------------------


def _tenant_branding_dto(program):
    """Program.ui_config → TenantBrandingDto 형태. 로고는 presigned URL로 반환해 R2 공개 도메인 404 방지."""
    from apps.infrastructure.storage import r2 as r2_storage

    cfg = getattr(program, "ui_config", None) or {}
    logo_url = r2_storage.resolve_admin_logo_url(
        logo_key=cfg.get("logo_key"),
        logo_url=cfg.get("logo_url"),
    )
    return {
        "tenantId": program.tenant_id,
        "loginTitle": cfg.get("login_title") or "",
        "loginSubtitle": cfg.get("login_subtitle") or "",
        "logoUrl": logo_url,
        "windowTitle": cfg.get("window_title") or "",
        "displayName": program.display_name,
    }


class TenantBrandingView(APIView):
    """
    GET/PATCH /api/v1/core/tenant-branding/<tenant_id>/
    dev_app 전용 — owner role만. Program.ui_config 기반.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndOwner]

    def _check_tenant_access(self, request, tenant_id: int):
        """같은 테넌트이거나 플랫폼 관리 테넌트인 경우만 허용."""
        if request.tenant.id != tenant_id and not is_platform_admin_tenant(request):
            return False
        return True

    def get(self, request, tenant_id: int):
        if not self._check_tenant_access(request, tenant_id):
            return Response({"detail": "Cross-tenant access denied."}, status=403)
        tenant = core_repo.tenant_get_by_id_any(tenant_id)
        if not tenant:
            return Response({"detail": "Tenant not found."}, status=404)
        program = core_repo.program_get_by_tenant(tenant)
        if not program:
            return Response({"detail": "Program not found for tenant."}, status=404)
        return Response(_tenant_branding_dto(program))

    def patch(self, request, tenant_id: int):
        if not self._check_tenant_access(request, tenant_id):
            return Response({"detail": "Cross-tenant access denied."}, status=403)
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
            if "logo_key" in cfg:
                del cfg["logo_key"]
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
    dev_app 전용 — owner role만.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndOwner]

    def post(self, request, tenant_id: int):
        if request.tenant.id != tenant_id and not is_platform_admin_tenant(request):
            return Response({"detail": "Cross-tenant access denied."}, status=403)
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

        # Admin 버킷은 공개 도메인이 비디오 버킷과 동일하게 설정된 경우가 많아 404 발생.
        # 항상 presigned URL 사용 (버킷별 공개 도메인 의존 제거).
        logo_url = r2_storage.generate_presigned_get_url_admin(key=key, expires_in=86400 * 7)

        cfg = dict(program.ui_config or {})
        cfg["logo_url"] = logo_url
        cfg["logo_key"] = key
        program.ui_config = cfg
        program.save(update_fields=["ui_config"])

        return Response({"logoUrl": logo_url})
