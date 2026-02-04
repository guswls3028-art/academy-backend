# ======================================================================
# PATH: apps/core/middleware/tenant.py
# ======================================================================
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from django.conf import settings
from django.http import JsonResponse

from apps.core.models import Tenant
from apps.core.tenant.context import set_current_tenant, clear_current_tenant
from apps.core.tenant.resolver import resolve_tenant_from_request
from apps.core.tenant.exceptions import TenantResolutionError


@dataclass(frozen=True)
class TenantResolutionResult:
    tenant: Optional[Tenant]
    reason: str


class TenantMiddleware:
    """
    ✅ Enterprise-grade Tenant Middleware (SSOT)

    Goals:
    - Request 단위로 tenant 확정 (request.tenant + contextvar)
    - 단일 테넌트(dev/초기 운영)에서는 헤더 없이도 "즉시 동작"
      - TENANT_DEFAULT_CODE 있으면 우선
      - 없으면 활성 tenant가 1개면 자동 선택
    - 멀티 테넌트 운영에서는 tenant header 강제 가능 (settings.TENANT_STRICT=True)

    Resolution priority:
    1) Header (default: X-Tenant-Code)
    2) Query param (optional: ?tenant=CODE)
    3) settings.TENANT_DEFAULT_CODE / env
    4) If active tenant count == 1 -> auto
    5) If exempt path -> tenant=None (bypass)
    6) Else error (400)

    Response:
    - X-Tenant-Code / X-Tenant-Id 헤더 주입 (tenant 있을 때)
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # 항상 초기화 (안전)
        clear_current_tenant()
        request.tenant = None  # type: ignore[attr-defined]

        try:
            tenant = resolve_tenant_from_request(request)
        except TenantResolutionError as e:
            return JsonResponse(
                {
                    "detail": "tenant resolution failed",
                    "code": e.code,
                    "message": e.message,
                },
                status=e.http_status,
            )

        # tenant context attach
        if tenant is not None:
            request.tenant = tenant  # type: ignore[attr-defined]
            set_current_tenant(tenant)

        response = self.get_response(request)

        # response headers (helpful for debugging / ops)
        if tenant is not None:
            response["X-Tenant-Code"] = tenant.code
            response["X-Tenant-Id"] = str(tenant.id)

        return response
