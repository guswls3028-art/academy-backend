# ======================================================================
# PATH: apps/core/tenant/resolver.py
# ======================================================================
from __future__ import annotations

from typing import Optional

from django.conf import settings

from apps.core.models import Tenant
from apps.core.tenant.exceptions import TenantResolutionError


def _normalize_code(v: object) -> str:
    return str(v or "").strip()


def _header_name() -> str:
    return str(getattr(settings, "TENANT_HEADER_NAME", "X-Tenant-Code") or "X-Tenant-Code").strip()


def _query_name() -> str:
    return str(getattr(settings, "TENANT_QUERY_PARAM_NAME", "tenant") or "tenant").strip()


def _default_code() -> str:
    return _normalize_code(getattr(settings, "TENANT_DEFAULT_CODE", ""))


def _strict_mode() -> bool:
    return bool(getattr(settings, "TENANT_STRICT", False))


def _allow_inactive() -> bool:
    return bool(getattr(settings, "TENANT_ALLOW_INACTIVE", False))


def _bypass_paths() -> list[str]:
    """
    Tenant가 없어도 되는 endpoint들.
    - 토큰 발급/갱신은 tenant 선행이 어려운 경우가 있어 기본 bypass
    - 내부 워커 루트는 기존 호환 위해 기본 bypass (worker가 헤더 보내면 자동 적용됨)
    """
    return list(
        getattr(
            settings,
            "TENANT_BYPASS_PATH_PREFIXES",
            [
                "/admin/",
                "/api/v1/token/",
                "/api/v1/token/refresh/",
                "/internal/",          # root-level internal (video worker)
                "/api/v1/internal/",   # api v1 internal (ai/video worker)
                "/swagger",
                "/redoc",
            ],
        )
    )


def _is_bypass_path(path: str) -> bool:
    p = str(path or "/")
    for prefix in _bypass_paths():
        if p.startswith(prefix):
            return True
    return False


def _get_header_value(request, header_name: str) -> str:
    # Django request.headers handles normalization
    v = request.headers.get(header_name) or ""
    return _normalize_code(v)


def _get_query_value(request, query_name: str) -> str:
    try:
        v = request.GET.get(query_name) or ""
    except Exception:
        v = ""
    return _normalize_code(v)


def _find_tenant_by_code(code: str) -> Optional[Tenant]:
    if not code:
        return None

    qs = Tenant.objects.filter(code=code)
    if not _allow_inactive():
        qs = qs.filter(is_active=True)
    return qs.first()


def _auto_pick_single_active_tenant() -> Optional[Tenant]:
    qs = Tenant.objects.filter(is_active=True).order_by("id")
    cnt = qs.count()
    if cnt == 1:
        return qs.first()
    return None


def resolve_tenant_from_request(request) -> Optional[Tenant]:
    """
    Returns:
      - Tenant instance, or
      - None (bypass path only)

    Raises:
      - TenantResolutionError
    """
    path = getattr(request, "path", "") or "/"

    # 0) bypass path: don't require tenant, but still allow if provided
    bypass = _is_bypass_path(path)

    header_name = _header_name()
    query_name = _query_name()

    # 1) explicit from header
    code = _get_header_value(request, header_name)
    if code:
        tenant = _find_tenant_by_code(code)
        if not tenant:
            # if exists but inactive, make it explicit
            exists = Tenant.objects.filter(code=code).exists()
            if exists:
                raise TenantResolutionError(
                    code="tenant_inactive",
                    message=f"Tenant '{code}' is inactive",
                    http_status=403,
                )
            raise TenantResolutionError(
                code="tenant_invalid",
                message=f"Tenant '{code}' not found",
                http_status=404,
            )
        return tenant

    # 2) optional query param
    code = _get_query_value(request, query_name)
    if code:
        tenant = _find_tenant_by_code(code)
        if not tenant:
            exists = Tenant.objects.filter(code=code).exists()
            if exists:
                raise TenantResolutionError(
                    code="tenant_inactive",
                    message=f"Tenant '{code}' is inactive",
                    http_status=403,
                )
            raise TenantResolutionError(
                code="tenant_invalid",
                message=f"Tenant '{code}' not found",
                http_status=404,
            )
        return tenant

    # 3) settings default code
    code = _default_code()
    if code:
        tenant = _find_tenant_by_code(code)
        if not tenant:
            exists = Tenant.objects.filter(code=code).exists()
            if exists:
                raise TenantResolutionError(
                    code="tenant_inactive",
                    message=f"Default tenant '{code}' is inactive",
                    http_status=403,
                )
            raise TenantResolutionError(
                code="tenant_invalid",
                message=f"Default tenant '{code}' not found",
                http_status=404,
            )
        return tenant

    # 4) auto-pick (single-tenant bootstrap)
    tenant = _auto_pick_single_active_tenant()
    if tenant:
        return tenant

    # 5) bypass path -> allow None
    if bypass:
        return None

    # 6) strict / non-strict handling
    # - strict: must specify tenant (and cannot infer)
    # - non-strict: still cannot proceed because ambiguous (multiple tenants)
    if _strict_mode():
        raise TenantResolutionError(
            code="tenant_missing",
            message=f"Tenant header '{header_name}' required",
            http_status=400,
        )

    raise TenantResolutionError(
        code="tenant_ambiguous",
        message=f"Tenant not resolved. Provide '{header_name}' header.",
        http_status=400,
    )
