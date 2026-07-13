# apps/core/authentication.py

from rest_framework.authentication import SessionAuthentication
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed


class TenantAwareSessionAuthentication(SessionAuthentication):
    """Django session authentication bound to active tenant authorization."""

    def authenticate(self, request):
        authenticated = super().authenticate(request)
        if authenticated is None:
            return None
        user, auth = authenticated
        tenant = getattr(request, "tenant", None)
        if tenant is None:
            raise AuthenticationFailed(
                "세션 요청의 학원 정보를 확인할 수 없습니다.",
                code="session_tenant_required",
            )
        from apps.core.services.tenant_access import user_has_active_tenant_access
        if not user_has_active_tenant_access(user, tenant):
            raise AuthenticationFailed(
                "이 학원에 대한 계정 권한이 만료되었습니다.",
                code="tenant_membership_inactive",
            )
        return user, auth


class TokenVersionJWTAuthentication(JWTAuthentication):
    """
    JWT 검증 시 token_version claim과 DB 값을 비교.
    비밀번호 변경 후 발급된 토큰만 유효하고, 이전 토큰은 즉시 무효화된다.

    추가: tenant_id claim과 현재 resolve된 tenant를 교차 검증.
    크로스테넌트 헤더 조작(X-Tenant-Code 변조) 방어 — 권한 단계 검증
    이전에 인증 단계에서 차단한다 (defense-in-depth).
    """

    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        claim_tv = validated_token.get("token_version")
        db_tv = getattr(user, "token_version", 0) or 0
        claim_tid = validated_token.get("tenant_id")
        if claim_tv is None or claim_tid is None:
            raise AuthenticationFailed(
                "필수 보안 정보가 없는 토큰입니다. 다시 로그인해 주세요.",
                code="token_claims_missing",
            )
        if claim_tv != db_tv:
            raise AuthenticationFailed(
                "세션이 만료되었습니다. 다시 로그인해 주세요.",
                code="token_version_mismatch",
            )

        # Required tenant_id claim cross-check.
        # 현재 tenant 컨텍스트는 tenant middleware(미들웨어 단계)에서 set 됨.
        try:
            from apps.core.tenant.context import get_current_tenant
            cur = get_current_tenant()
            if claim_tid is not None and cur is not None and int(claim_tid) != int(cur.id):
                raise AuthenticationFailed(
                    "토큰과 학원 정보가 일치하지 않습니다.",
                    code="tenant_mismatch",
                )
            access_tenant = cur
            if access_tenant is None and claim_tid is not None:
                from academy.adapters.db.django import repositories_core as core_repo
                access_tenant = core_repo.tenant_get_by_id(claim_tid)
                if access_tenant is None:
                    raise AuthenticationFailed(
                        "토큰의 학원 정보가 만료되었거나 비활성 상태입니다.",
                        code="tenant_inactive",
                    )
            if access_tenant is not None:
                from apps.core.services.tenant_access import user_has_active_tenant_access
                if not user_has_active_tenant_access(user, access_tenant):
                    raise AuthenticationFailed(
                        "이 학원에 대한 계정 권한이 만료되었습니다.",
                        code="tenant_membership_inactive",
                    )
        except AuthenticationFailed:
            raise
        except (TypeError, ValueError):
            raise AuthenticationFailed(
                "토큰의 학원 정보가 올바르지 않습니다.",
                code="tenant_mismatch",
            )
        return user
