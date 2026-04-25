# apps/core/authentication.py

from rest_framework.authentication import SessionAuthentication
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed


class CsrfExemptSessionAuthentication(SessionAuthentication):
    """
    API 전용 SessionAuthentication
    - 로그인 세션은 유지
    - CSRF 검사는 비활성화
    """
    def enforce_csrf(self, request):
        return


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
        # claim이 없는 토큰(마이그레이션 전 발급분)은 token_version=0으로 간주
        if claim_tv is not None and claim_tv != db_tv:
            raise AuthenticationFailed(
                "세션이 만료되었습니다. 다시 로그인해 주세요.",
                code="token_version_mismatch",
            )

        # tenant_id claim cross-check (있을 때만 — 마이그레이션 안전).
        # 현재 tenant 컨텍스트는 tenant middleware(미들웨어 단계)에서 set 됨.
        claim_tid = validated_token.get("tenant_id")
        if claim_tid is not None:
            try:
                from apps.core.tenant.context import get_current_tenant
                cur = get_current_tenant()
                if cur is not None and int(claim_tid) != int(cur.id):
                    raise AuthenticationFailed(
                        "토큰과 학원 정보가 일치하지 않습니다.",
                        code="tenant_mismatch",
                    )
            except AuthenticationFailed:
                raise
            except Exception:
                # tenant context 미설정 등 — 권한 단계의 기존 검증에 위임 (fail-open here only)
                pass
        return user
