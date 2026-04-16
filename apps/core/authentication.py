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
        return user
