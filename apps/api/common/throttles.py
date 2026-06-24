# PATH: apps/api/common/throttles.py
"""
SMS/인증 엔드포인트 전용 throttle.

비인증 엔드포인트(AllowAny)에서 SMS 발송·비밀번호 변경이 가능하므로,
글로벌 AnonRateThrottle(60/min)보다 훨씬 엄격한 제한 적용.
"""
from rest_framework.throttling import SimpleRateThrottle


class SmsEndpointThrottle(SimpleRateThrottle):
    """
    SMS 발송 엔드포인트 전용: IP 기준 5회/시간.
    대상: SendExistingCredentials, PasswordFindRequest, PasswordResetSend
    """
    scope = "sms_endpoint"
    rate = "5/hour"

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


class StaffPasswordResetThrottle(SimpleRateThrottle):
    """
    Staff-side student/parent password reset: tenant+user 기준 60회/시간.

    Public account recovery remains on SmsEndpointThrottle's stricter IP bucket.
    Staff users often process several student/parent resets from one academy
    network, so sharing the public SMS IP bucket causes normal work to hit 429.
    """
    scope = "staff_password_reset"
    rate = "60/hour"

    def get_cache_key(self, request, view):
        user = getattr(request, "user", None)
        tenant = getattr(request, "tenant", None)
        if not user or not user.is_authenticated or not tenant:
            return None
        return self.cache_format % {
            "scope": self.scope,
            "ident": f"{tenant.pk}:{user.pk}",
        }


class LoginThrottle(SimpleRateThrottle):
    """
    로그인 엔드포인트 전용: IP 기준 10회/분.
    brute force 방어. 4자 이상 비밀번호 정책에서 특히 중요.
    """
    scope = "login"
    rate = "10/minute"

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


class SignupCheckThrottle(SimpleRateThrottle):
    """
    회원가입 중복검사 전용: IP 기준 30회/분.
    대상: check_duplicate (존재 여부만 반환, SMS 미발송)
    """
    scope = "signup_check"
    rate = "30/minute"

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


class ChangePasswordThrottle(SimpleRateThrottle):
    """
    비밀번호 변경 전용: 사용자(또는 IP) 기준 10회/시간.
    활성 세션 탈취 후 brute force 방지층.
    """
    scope = "change_password"
    rate = "10/hour"

    def get_cache_key(self, request, view):
        ident = (
            str(request.user.pk)
            if request.user and request.user.is_authenticated
            else self.get_ident(request)
        )
        return self.cache_format % {
            "scope": self.scope,
            "ident": ident,
        }
