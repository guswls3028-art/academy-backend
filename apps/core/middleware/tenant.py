# PATH: apps/core/middleware/tenant.py
from __future__ import annotations

import logging

from django.http import JsonResponse

from apps.core.tenant.context import set_current_tenant, clear_current_tenant
from apps.core.tenant.resolver import resolve_tenant_from_request
from apps.core.tenant.exceptions import TenantResolutionError

logger = logging.getLogger(__name__)

# 테넌트 해석 없이 통과시키는 경로 (ALB/컨테이너 health check용)
BYPASS_PATHS = {"/health", "/health/", "/healthz", "/healthz/", "/readyz", "/readyz/"}


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

        # Health 계열은 어떤 경우에도 tenant resolve 금지 (DB 의존성/Host strictness 회피)
        path = (getattr(request, "path", "") or "/").strip() or "/"
        norm = path.rstrip("/") or "/"
        if path in BYPASS_PATHS or norm in ("/health", "/healthz", "/readyz"):
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
            if e.code == "tenant_invalid" and payload.get("host") in ("localhost", "127.0.0.1"):
                payload["hint"] = "Run: python manage.py ensure_localhost_tenant"
            return JsonResponse(payload, status=e.http_status)
        except Exception as e:
            logger.exception("Tenant resolution unexpected error: %s", e)
            host = getattr(request, "META", {}).get("HTTP_HOST", "")
            payload = {
                "detail": "tenant resolution failed",
                "code": "server_error",
                "message": str(e),
            }
            if host:
                payload["host"] = host.split(":")[0].strip().lower()
            return JsonResponse(payload, status=500)

        try:
            if tenant is not None:
                request.tenant = tenant  # type: ignore[attr-defined]
                set_current_tenant(tenant)

                # ── 구독 만료 체크 (로그인/공개 페이지는 허용) ──
                if not _is_subscription_exempt_path(path):
                    subscription_err = _check_subscription(tenant, request)
                    if subscription_err is not None:
                        return subscription_err

            response = self.get_response(request)

            if tenant is not None:
                response["X-Tenant-Code"] = tenant.code
                response["X-Tenant-Id"] = str(tenant.id)

            return response
        finally:
            # ✅ 어떤 예외/리턴 경로든 tenant 컨텍스트 종료
            clear_current_tenant()


# ── 구독 만료 시 허용할 경로 (로그인, 인증, 헬스 등) ──
_SUBSCRIPTION_EXEMPT_PREFIXES = (
    "/api/v1/auth/",
    "/api/v1/token/",
    "/api/v1/students/registration",
    "/api/v1/students/password",
    "/api/v1/core/me",
    "/api/v1/core/program",
    "/api/v1/core/subscription",
    "/admin/",
    "/static/",
    "/media/",
)


def _is_subscription_exempt_path(path: str) -> bool:
    """구독 체크를 면제할 경로 (로그인, 프로필, 구독 정보 조회 등)"""
    for prefix in _SUBSCRIPTION_EXEMPT_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


def _check_subscription(tenant, request) -> JsonResponse | None:
    """
    구독 만료 검사.
    - Program이 없으면 통과 (하위 호환)
    - 만료 시 402 Payment Required 반환
    """
    try:
        program = getattr(tenant, "program", None)
        if program is None:
            return None  # Program 없으면 통과

        if program.is_subscription_active:
            return None  # 구독 유효

        # 만료 — 402 반환
        return JsonResponse(
            {
                "detail": "구독이 만료되었습니다. 관리자에게 문의하거나 구독을 갱신해 주세요.",
                "code": "subscription_expired",
                "plan": program.plan,
                "expires_at": str(program.subscription_expires_at) if program.subscription_expires_at else None,
            },
            status=402,
        )
    except Exception as e:
        # 구독 체크 실패 시에는 통과 (서비스 가용성 우선)
        logger.warning("Subscription check failed for tenant %s: %s", tenant.code, e)
        return None
