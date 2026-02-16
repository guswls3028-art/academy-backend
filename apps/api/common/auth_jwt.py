# JWT 발급 시 테넌트별 User 조회. 1테넌트=1프로그램 격리.
from __future__ import annotations

from academy.adapters.db.django import repositories_core as core_repo
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework import serializers


def _tenant_for_auth(request):
    """로그인 요청에서 테넌트 결정: X-Tenant-Code 헤더 또는 body tenant_code."""
    raw = (
        (request.META.get("HTTP_X_TENANT_CODE") or "").strip()
        or (getattr(request, "data", None) or {}).get("tenant_code") or ""
    )
    if isinstance(raw, str):
        raw = raw.strip()
    return core_repo.tenant_get_by_code(raw) if raw else None


class TenantAwareTokenObtainPairSerializer(TokenObtainPairSerializer):
    """테넌트별 User만 로그인 허용. tenant=null 계정은 로그인 불가."""

    def validate(self, attrs):
        request = self.context.get("request")
        username = (attrs.get("username") or "").strip()
        password = attrs.get("password") or ""

        tenant = _tenant_for_auth(request) if request else None
        if not tenant:
            raise serializers.ValidationError(
                {"detail": "테넌트(학원) 정보가 필요합니다. 로그인 페이지에서 학원을 선택해 주세요."},
                code="authorization",
            )

        user = core_repo.user_get_by_tenant_username(tenant, username)
        if not user or not user.check_password(password):
            raise serializers.ValidationError(
                {"detail": "로그인 아이디 또는 비밀번호가 올바르지 않습니다."},
                code="authorization",
            )
        if user.tenant_id is None:
            raise serializers.ValidationError(
                {"detail": "로그인할 수 없는 계정입니다."},
                code="authorization",
            )
        if not user.is_active:
            raise serializers.ValidationError(
                {"detail": "비활성화된 계정입니다."},
                code="authorization",
            )

        refresh = self.get_token(user)
        return {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
        }


class TenantAwareTokenObtainPairView(TokenObtainPairView):
    serializer_class = TenantAwareTokenObtainPairSerializer
