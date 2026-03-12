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


class UnhandledExceptionMiddleware:
    """미처리 예외를 500 JSON으로 변환. process_exception 응답에 CORS 헤더를 직접 붙임."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        logger.exception("Unhandled exception: %s", exception)
        resp = JsonResponse(
            {"detail": "서버 오류가 발생했습니다.", "error": str(exception)},
            status=500,
        )
        return _add_cors_headers_to_response(request, resp)
