# PATH: apps/core/middleware/tenant.py
from __future__ import annotations

from django.http import JsonResponse

from apps.core.tenant.context import set_current_tenant, clear_current_tenant
from apps.core.tenant.resolver import resolve_tenant_from_request
from apps.core.tenant.exceptions import TenantResolutionError


# 테넌트 해석 없이 통과시키는 경로 (ALB/컨테이너 health check용)
BYPASS_PATHS = {"/health", "/health/"}


class TenantMiddleware:
    """
    Enterprise Tenant Middleware (Domain 1:1)

    - Request 단위 tenant 확정
    - tenant source: request.get_host() -> TenantDomain.host
    - bypass path만 tenant=None 허용

    ✅ 개선(봉인 레벨):
    - request 처리 종료 후, 어떤 경우에도 clear_current_tenant() (finally)
      -> contextvars 누수/오염 방지
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        clear_current_tenant()
        request.tenant = None  # type: ignore[attr-defined]

        if request.path in BYPASS_PATHS:
            try:
                return self.get_response(request)
            finally:
                clear_current_tenant()

        try:
            tenant = resolve_tenant_from_request(request)
        except TenantResolutionError as e:
            # tenant context는 이미 clear 상태 (403=tenant_inactive, 404=tenant_invalid 등)
            payload = {
                "detail": "tenant resolution failed",
                "code": e.code,
                "message": e.message,
            }
            host = getattr(request, "META", {}).get("HTTP_HOST", "")
            if host:
                payload["host"] = host.split(":")[0].strip().lower()
            return JsonResponse(payload, status=e.http_status)

        try:
            if tenant is not None:
                request.tenant = tenant  # type: ignore[attr-defined]
                set_current_tenant(tenant)

            response = self.get_response(request)

            if tenant is not None:
                response["X-Tenant-Code"] = tenant.code
                response["X-Tenant-Id"] = str(tenant.id)

            return response
        finally:
            # ✅ 어떤 예외/리턴 경로든 tenant 컨텍스트 종료
            clear_current_tenant()
