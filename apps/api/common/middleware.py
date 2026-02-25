# apps/api/common/middleware.py
# 뷰에서 미처리 예외 발생 시 500 JSON 반환.
# process_exception 응답은 CorsMiddleware를 거치지 않으므로 여기서 CORS 헤더 추가.
# CorsResponseFixMiddleware: 모든 응답(5xx 포함)에 CORS 헤더가 빠졌을 때 보강.
from __future__ import annotations

import logging

from django.conf import settings
from django.http import JsonResponse

logger = logging.getLogger(__name__)


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
