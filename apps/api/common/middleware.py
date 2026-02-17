# apps/api/common/middleware.py
# 뷰에서 미처리 예외 발생 시 500 JSON 반환 → CORS 헤더가 붙어 502 대신 500으로 응답
from __future__ import annotations

import logging

from django.http import JsonResponse

logger = logging.getLogger(__name__)


class UnhandledExceptionMiddleware:
    """미처리 예외를 500 JSON으로 변환. CORS는 CorsMiddleware가 이 응답에 붙임."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        logger.exception("Unhandled exception: %s", exception)
        return JsonResponse(
            {"detail": "서버 오류가 발생했습니다.", "error": str(exception)},
            status=500,
        )
