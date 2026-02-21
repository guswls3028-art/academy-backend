# PATH: apps/core/permissions/internal.py
from rest_framework.permissions import BasePermission

from django.conf import settings


class IsLambdaInternal(BasePermission):
    """
    Lambda 전용 internal API 인증.
    X-Internal-Key 헤더가 LAMBDA_INTERNAL_API_KEY와 일치할 때만 허용.
    LAMBDA_INTERNAL_API_KEY 미설정 시 모든 요청 차단.
    """

    message = "Lambda internal API key required."

    def has_permission(self, request, view):
        key = getattr(settings, "LAMBDA_INTERNAL_API_KEY", None)
        if not key:
            return False
        return request.headers.get("X-Internal-Key") == key
