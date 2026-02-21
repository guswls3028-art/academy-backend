# apps/api/common/middleware.py
# 뷰에서 미처리 예외 발생 시 500 JSON 반환.
# process_exception 응답은 CorsMiddleware를 거치지 않으므로 여기서 CORS 헤더 추가.
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
    # preflight가 이미 통과한 요청이므로 Allow-Headers는 생략해도 됨 (필요 시 추가 가능)
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
