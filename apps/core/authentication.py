# apps/core/authentication.py

from rest_framework.authentication import SessionAuthentication


class CsrfExemptSessionAuthentication(SessionAuthentication):
    """
    API 전용 SessionAuthentication
    - 로그인 세션은 유지
    - CSRF 검사는 비활성화
    """
    def enforce_csrf(self, request):
        return
