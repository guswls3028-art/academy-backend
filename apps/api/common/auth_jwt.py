# JWT 발급 시 테넌트별 User 조회. 1테넌트=1프로그램 격리.
from __future__ import annotations

from django.conf import settings

from academy.adapters.db.django import repositories_core as core_repo
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework import serializers

from apps.core.services.password import (
    consume_pending_password_reset,
    pending_password_reset_matches,
)


def _tenant_for_auth(request):
    """
    로그인 시 테넌트 결정 (SSOT — host 우선).

    /api/v1/token/ 은 TenantMiddleware bypass 경로라 request.tenant 가 None.
    여기서 다음 우선순위로 직접 resolve:

    1) request.tenant: 만약 미들웨어가 이미 결정한 경우 그대로 사용 (안전망).
    2) host → TenantDomain 매핑이 있으면 그 테넌트 (운영 SSOT — tchul.com / limglish.kr 등).
    3) host 가 header-allowed 목록(api.hakwonplus.com / localhost / *.elb.amazonaws.com)인
       경우에만 X-Tenant-Code 헤더 또는 body tenant_code 를 사용.
    4) 그 외 host 에서 헤더/body 입력은 무시 — 임의 도메인에서 다른 테넌트 계정으로의
       로그인 시도(테넌트 enumeration / 브루트포스 표면 확장)를 차단.
    """
    tenant = getattr(request, "tenant", None)
    if tenant is not None:
        return tenant

    host = ""
    try:
        host = (request.get_host() or "").split(":")[0].strip().lower()
    except Exception:
        host = ""

    # 2) host → TenantDomain (운영 도메인은 여기서 결정)
    if host:
        try:
            from apps.core.tenant.resolver import _resolve_tenant_from_host
            t = _resolve_tenant_from_host(host)
            if t is not None:
                return t
        except Exception:
            pass

    # 3) header-allowed host 에서만 헤더/body fallback
    allowed_hosts = getattr(
        settings,
        "TENANT_HEADER_CODE_ALLOWED_HOSTS",
        ("api.hakwonplus.com",),
    )
    allowed = host in allowed_hosts or host.endswith(".elb.amazonaws.com")
    if not allowed:
        return None

    raw = (
        (request.META.get("HTTP_X_TENANT_CODE") or "").strip()
        or (getattr(request, "data", None) or {}).get("tenant_code") or ""
    )
    if isinstance(raw, str):
        raw = raw.strip()
    return core_repo.tenant_get_by_code(raw) if raw else None


class TenantAwareTokenObtainPairSerializer(TokenObtainPairSerializer):
    """테넌트별 User만 로그인 허용. tenant=null 계정은 로그인 불가."""

    @staticmethod
    def _password_matches(user, password: str, *, consume_pending: bool = True) -> bool:
        if not user:
            return False
        if user.check_password(password):
            return True
        if consume_pending:
            return consume_pending_password_reset(user, password)
        return pending_password_reset_matches(user, password)

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
        # 학부모: ID = 학부모 전화번호. username이 전화번호일 때 Parent로 조회 후 해당 User로 인증
        # 학생 로그인ID와 학부모 전화번호가 동일할 수 있으므로, 첫 매칭 실패 시 학부모도 시도
        parent = core_repo.parent_get_by_tenant_phone(tenant, username)
        if not user:
            if parent and parent.user_id:
                user = parent.user
        elif not self._password_matches(user, password, consume_pending=False):
            if parent and parent.user_id and self._password_matches(parent.user, password, consume_pending=False):
                user = parent.user
        if not user or not self._password_matches(user, password, consume_pending=False):
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
        if not self._password_matches(user, password):
            raise serializers.ValidationError(
                {"detail": "로그인 아이디 또는 비밀번호가 올바르지 않습니다."},
                code="authorization",
            )

        refresh = self.get_token(user)
        # JWT에 tenant_id 클레임 추가 — 크로스테넌트 헤더 조작 방어
        if user.tenant_id is not None:
            refresh["tenant_id"] = user.tenant_id
            refresh.access_token["tenant_id"] = user.tenant_id
        # token_version: 비밀번호 변경 시 기존 토큰 무효화용
        tv = getattr(user, "token_version", 0) or 0
        refresh["token_version"] = tv
        refresh.access_token["token_version"] = tv
        # mcp(must_change_password) — 초기 비번 강제 변경 게이트(MustChangePasswordGate)에서 사용.
        # change_password 후 토큰 무효화 → 새 토큰엔 mcp=0 으로 자동 반영.
        mcp = bool(getattr(user, "must_change_password", False))
        refresh["mcp"] = mcp
        refresh.access_token["mcp"] = mcp
        return {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
        }


class TenantAwareTokenObtainPairView(TokenObtainPairView):
    serializer_class = TenantAwareTokenObtainPairSerializer
    throttle_classes = []  # __init__에서 설정

    def get_throttles(self):
        from apps.api.common.throttles import LoginThrottle
        return [LoginThrottle()]
