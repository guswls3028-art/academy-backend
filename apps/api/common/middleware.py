# apps/api/common/middleware.py
# 뷰에서 미처리 예외 발생 시 500 JSON 반환.
# process_exception 응답은 CorsMiddleware를 거치지 않으므로 여기서 CORS 헤더 추가.
# CorsResponseFixMiddleware: 모든 응답(5xx 포함)에 CORS 헤더가 빠졌을 때 보강.
# HealthCheckHostMiddleware: ALB/인스턴스 헬스체크가 Host: private IP 로 오면 ALLOWED_HOSTS 400 방지.
from __future__ import annotations

import logging

from django.conf import settings
from django.http import JsonResponse

logger = logging.getLogger(__name__)

HEALTH_CHECK_PATHS = ("/health", "/health/", "/healthz", "/healthz/", "/readyz", "/readyz/")


def _is_health_check_path(path: str) -> bool:
    """ALB/헬스체크 경로 여부. trailing slash·정규화 포함."""
    if not path:
        return False
    norm = path.rstrip("/") or "/"
    return norm in ("/health", "/healthz", "/readyz") or path in HEALTH_CHECK_PATHS


class HealthCheckHostMiddleware:
    """
    ALB target health check 시 Host 가 인스턴스 private IP 이면 ALLOWED_HOSTS 에 없어 400 발생.
    Health check 경로는 Host 를 127.0.0.1 로 덮어 통과 (prod 에 127.0.0.1 포함).

    주의:
    - settings 에서 USE_X_FORWARDED_HOST=True 인 경우, Django 는 HTTP_X_FORWARDED_HOST 를 우선한다.
      따라서 HTTP_HOST 만 바꾸면 CommonMiddleware 단계에서 DisallowedHost(400)가 계속 발생할 수 있다.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if _is_health_check_path(request.path):
            # Django host validation(CommonMiddleware 등) 우회: forwarded-host 포함 정규화
            request.META["HTTP_HOST"] = "127.0.0.1"
            request.META["HTTP_X_FORWARDED_HOST"] = "127.0.0.1"
        return self.get_response(request)


def _add_cors_headers_to_response(request, response):
    """
    process_exception 등으로 만든 응답에 CORS 헤더 추가.
    브라우저가 500 응답도 읽을 수 있도록 함 (No 'Access-Control-Allow-Origin' 방지).
    """
    origin = (request.META.get("HTTP_ORIGIN") or "").strip()
    allowed = getattr(settings, "CORS_ALLOWED_ORIGINS", []) or []
    if origin and origin in allowed:
        response["Access-Control-Allow-Origin"] = origin
    elif allowed:
        response["Access-Control-Allow-Origin"] = allowed[0]
    if getattr(settings, "CORS_ALLOW_CREDENTIALS", False):
        response["Access-Control-Allow-Credentials"] = "true"
    vary = (response.get("Vary") or "").strip()
    if "Origin" not in vary:
        response["Vary"] = f"{vary}, Origin".lstrip(", ")
    return response


class SecurityHeadersMiddleware:
    """
    모든 응답에 보안 헤더 추가.
    - X-Content-Type-Options: nosniff → 브라우저 MIME 스니핑 차단.
      보안 제품이 application/octet-stream + MIME 스니핑 가능 응답을 malware로 분류하는 것을 방지.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response["X-Content-Type-Options"] = "nosniff"
        return response


class CorsResponseFixMiddleware:
    """
    모든 응답에서 Access-Control-Allow-Origin 이 없고, 요청 Origin 이 허용 목록에 있으면 CORS 헤더 추가.
    django-cors-headers 는 보통 2xx만 처리하므로 5xx 응답에 CORS가 빠져 브라우저가 'blocked by CORS policy' 하는 경우 방지.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if response.get("Access-Control-Allow-Origin"):
            return response
        origin = (request.META.get("HTTP_ORIGIN") or "").strip()
        allowed = getattr(settings, "CORS_ALLOWED_ORIGINS", []) or []
        if not origin or origin not in allowed:
            return response
        response["Access-Control-Allow-Origin"] = origin
        if getattr(settings, "CORS_ALLOW_CREDENTIALS", False):
            response["Access-Control-Allow-Credentials"] = "true"
        vary = (response.get("Vary") or "").strip()
        if "Origin" not in vary:
            response["Vary"] = f"{vary}, Origin".lstrip(", ")
        return response


class SentryContextMiddleware:
    """Sentry scope에 tenant_id, correlation_id, user_id 주입."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            import sentry_sdk
            with sentry_sdk.configure_scope() as scope:
                tenant = getattr(request, "tenant", None)
                if tenant:
                    scope.set_tag("tenant_id", str(tenant.id))
                    scope.set_tag("tenant_code", getattr(tenant, "code", ""))
                user = getattr(request, "user", None)
                if user and getattr(user, "is_authenticated", False):
                    scope.set_user({"id": str(user.id)})
                from apps.api.common.correlation import get_correlation_id
                scope.set_tag("correlation_id", get_correlation_id())
        except Exception:
            pass
        return self.get_response(request)


class MustChangePasswordGate:
    """JWT mcp=True 클레임 보유 시 비밀번호 변경/로그아웃/refresh/me 외 모든 경로 403.

    의도(2026-05-12 직전 rebase commit 잔재):
    - 신규 가입 학생 등에게 임시 비밀번호 부여 → mcp 클레임 박힌 토큰 발급 →
      로그인된 상태에서도 다른 데이터 접근 금지, 비번 변경 강제.
    - 토큰에 mcp가 없거나 False면 무조건 통과(기존 사용자 영향 0).
    - Authorization 헤더 없으면 통과(인증 단계는 DRF가 처리).

    회귀 spec: apps/api/common/tests/test_must_change_password_gate.py
    """

    BYPASS_PREFIXES = (
        "/admin",                  # Django admin
        "/api/v1/internal",        # 내부 운영 호출
        "/health",
        "/healthz",
        "/readyz",
        "/api/v1/token",           # JWT refresh
        "/api/v1/auth/logout",     # 로그아웃 허용
        "/api/v1/auth/login",      # 로그인 자체는 게이트 무관
    )
    ALLOW_EXACT = (
        "/api/v1/auth/change-password/",
        "/api/v1/auth/change-password",
        "/api/v1/core/change-password/",
        "/api/v1/core/change-password",
        "/api/v1/core/me/profile/change-password/",
        "/api/v1/core/me/profile/change-password",
        "/api/v1/core/me/",
        "/api/v1/core/me",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        auth = request.META.get("HTTP_AUTHORIZATION") or ""
        if not auth.startswith("Bearer "):
            return self.get_response(request)
        path = request.path or ""
        # bypass prefix 우선 — admin/internal/health/token 등은 비번 변경 게이트와 무관.
        for p in self.BYPASS_PREFIXES:
            if path.startswith(p):
                return self.get_response(request)
        # mcp 클레임 추출 — 토큰 파싱 실패는 게이트 통과(DRF 단계에서 401 처리).
        try:
            from rest_framework_simplejwt.tokens import UntypedToken
            raw = auth.split(" ", 1)[1].strip()
            tok = UntypedToken(raw)
            mcp = bool(tok.get("mcp"))
        except Exception:
            return self.get_response(request)
        if not mcp:
            return self.get_response(request)
        # mcp=True — 정확 일치 경로만 통과.
        if path in self.ALLOW_EXACT:
            return self.get_response(request)
        return JsonResponse(
            {"code": "must_change_password", "detail": "비밀번호를 먼저 변경해야 합니다."},
            status=403,
        )


class UnhandledExceptionMiddleware:
    """미처리 예외를 500 JSON으로 변환. process_exception 응답에 CORS 헤더를 직접 붙임."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        logger.exception("Unhandled exception: %s", exception)
        # 운영(DEBUG=False)에서는 내부 예외 메시지를 응답에 노출하지 않는다.
        # correlation_id 는 항상 포함해 운영 디버깅 시 로그와 매칭 가능.
        try:
            from apps.api.common.correlation import get_correlation_id
            cid = get_correlation_id() or ""
        except Exception:
            cid = ""
        body = {
            "detail": "서버 오류가 발생했습니다.",
            "correlation_id": cid,
        }
        if getattr(settings, "DEBUG", False):
            body["error"] = str(exception)
        resp = JsonResponse(body, status=500)
        return _add_cors_headers_to_response(request, resp)
