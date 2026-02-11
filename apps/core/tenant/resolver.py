# PATH: apps/core/tenant/resolver.py
from __future__ import annotations

from typing import Optional

from django.conf import settings

from apps.core.models import Tenant, TenantDomain
from apps.core.tenant.exceptions import TenantResolutionError


def _is_bypass_path(path: str) -> bool:
    p = str(path or "/")
    for prefix in getattr(settings, "TENANT_BYPASS_PATH_PREFIXES", []):
        if p.startswith(prefix):
            return True
    return False


def _normalize_host(host: str) -> str:
    """
    - 포트 제거
    - 소문자 정규화
    """
    if not host:
        return ""
    return host.split(":")[0].strip().lower()


def _resolve_tenant_from_host(host: str) -> Optional[Tenant]:
    """
    Host -> TenantDomain.host -> Tenant (SSOT)

    ✅ 에러 분기 (Enterprise):
    - domain row 없음            -> None
    - domain inactive            -> TenantResolutionError(tenant_inactive)
    - tenant inactive            -> TenantResolutionError(tenant_inactive)
    - (DB 무결성 깨짐) 중복 host  -> TenantResolutionError(tenant_ambiguous)
    """
    if not host:
        return None

    qs = TenantDomain.objects.select_related("tenant").filter(host=host)
    # host 는 unique 가 정상이나, 운영 사고/수동 SQL 등 최악의 상황 대비
    cnt = qs.count()
    if cnt == 0:
        return None
    if cnt > 1:
        raise TenantResolutionError(
            code="tenant_ambiguous",
            message=f"Multiple TenantDomain rows exist for host '{host}'",
            http_status=500,
        )

    td = qs.first()
    if td is None:
        return None

    if not td.is_active:
        raise TenantResolutionError(
            code="tenant_inactive",
            message=f"TenantDomain is inactive for host '{host}'",
            http_status=403,
        )

    if not td.tenant or not td.tenant.is_active:
        raise TenantResolutionError(
            code="tenant_inactive",
            message=f"Tenant is inactive for host '{host}'",
            http_status=403,
        )

    return td.tenant


def resolve_tenant_from_request(request) -> Optional[Tenant]:
    """
    Enterprise Resolver (Domain 1:1 with operational flexibility)

    Rules:
    - tenant는 request.get_host() -> TenantDomain.host 로만 결정
    - fallback 없음
    - bypass path만 tenant=None 허용
    """
    path = getattr(request, "path", "") or "/"
    bypass = _is_bypass_path(path)

    host = _normalize_host(request.get_host())

    try:
        tenant = _resolve_tenant_from_host(host)
    except TenantResolutionError:
        # 여기서 그대로 propagate (middleware가 ops-friendly JSON으로 변환)
        raise

    if tenant:
        return tenant

    if bypass:
        return None

    raise TenantResolutionError(
        code="tenant_invalid",
        message=f"Tenant not found for host '{host}'",
        http_status=404,
    )
