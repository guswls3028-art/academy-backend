# PATH: apps/api/common/throttles.py
"""
SMS/인증 엔드포인트 전용 throttle.

비인증 엔드포인트(AllowAny)에서 SMS 발송·비밀번호 변경이 가능하므로,
글로벌 AnonRateThrottle(60/min)보다 훨씬 엄격한 제한 적용.
"""
import hashlib
import hmac
import logging
import unicodedata
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.throttling import BaseThrottle, SimpleRateThrottle

logger = logging.getLogger(__name__)


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


def _login_bucket_key(scope: str, material: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        f"login-throttle:{scope}:{material}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _prune_expired_login_buckets() -> None:
    """Bound hostile-identity storage growth without a per-request cleanup query."""

    from apps.core.models import LoginThrottleBucket

    cutoff = timezone.now() - timedelta(days=1)
    stale_keys = list(
        LoginThrottleBucket.objects.filter(expires_at__lt=cutoff)
        .order_by("expires_at")
        .values_list("bucket_key", flat=True)[:500]
    )
    if stale_keys:
        LoginThrottleBucket.objects.filter(
            bucket_key__in=stale_keys,
            expires_at__lt=cutoff,
        ).delete()


def _consume_login_bucket(
    *,
    scope: str,
    material: str,
    limit: int,
    window_seconds: int,
) -> tuple[bool, float]:
    """Atomically consume one shared RDS-backed throttle slot."""

    from apps.core.models import LoginThrottleBucket

    now = timezone.now()
    expires_at = now + timedelta(seconds=window_seconds)
    bucket_key = _login_bucket_key(scope, material)
    with transaction.atomic():
        bucket = (
            LoginThrottleBucket.objects.select_for_update()
            .filter(bucket_key=bucket_key)
            .first()
        )
        if bucket is None:
            try:
                with transaction.atomic():
                    LoginThrottleBucket.objects.create(
                        bucket_key=bucket_key,
                        scope=scope,
                        request_count=1,
                        window_started_at=now,
                        expires_at=expires_at,
                    )
                if bucket_key.startswith("00"):
                    transaction.on_commit(_prune_expired_login_buckets)
                return True, 0.0
            except IntegrityError:
                bucket = LoginThrottleBucket.objects.select_for_update().get(
                    bucket_key=bucket_key
                )

        if bucket.expires_at <= now:
            bucket.scope = scope
            bucket.request_count = 1
            bucket.window_started_at = now
            bucket.expires_at = expires_at
            bucket.save(
                update_fields=[
                    "scope",
                    "request_count",
                    "window_started_at",
                    "expires_at",
                    "updated_at",
                ]
            )
            return True, 0.0

        wait_seconds = max(1.0, (bucket.expires_at - now).total_seconds())
        if bucket.request_count >= limit:
            return False, wait_seconds

        bucket.request_count += 1
        bucket.save(update_fields=["request_count", "updated_at"])
        return True, 0.0


def _normalized_login_account(request) -> str:
    data = getattr(request, "data", None) or {}
    username = unicodedata.normalize(
        "NFKC", str(data.get("username") or "").strip()
    ).casefold()
    if not username:
        return ""
    tenant_hint = (
        str(data.get("tenant_code") or data.get("tenant") or "").strip().casefold()
        or str(request.META.get("HTTP_X_TENANT_CODE") or "").strip().casefold()
    )
    if not tenant_hint:
        try:
            tenant_hint = request.get_host().split(":", 1)[0].strip().casefold()
        except Exception:
            tenant_hint = "unknown-host"
    return f"{tenant_hint}:{username}"


class LoginThrottle(BaseThrottle):
    """
    로그인 엔드포인트 전용 분산 제한.

    - 검증된 client IP 기준 60회/분
    - tenant+계정 기준 10회/5분

    RDS 행 잠금으로 API 인스턴스 3대와 배포 재시작을 가로질러 공유된다.
    버킷 키는 SECRET_KEY HMAC만 저장하여 로그인 ID/IP 원문을 남기지 않는다.
    """

    IP_LIMIT = 60
    IP_WINDOW_SECONDS = 60
    ACCOUNT_LIMIT = 10
    ACCOUNT_WINDOW_SECONDS = 300

    def __init__(self):
        self._wait = 0.0

    def allow_request(self, request, view):
        from apps.core.services.client_ip import get_client_ip

        ip = get_client_ip(request) or "unknown"
        allowed, wait = _consume_login_bucket(
            scope="ip",
            material=ip,
            limit=self.IP_LIMIT,
            window_seconds=self.IP_WINDOW_SECONDS,
        )
        if not allowed:
            self._wait = wait
            return False

        account = _normalized_login_account(request)
        if not account:
            return True
        allowed, wait = _consume_login_bucket(
            scope="account",
            material=account,
            limit=self.ACCOUNT_LIMIT,
            window_seconds=self.ACCOUNT_WINDOW_SECONDS,
        )
        if not allowed:
            self._wait = wait
            return False
        return True

    def wait(self):
        return self._wait or None


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


class TossWebhookThrottle(SimpleRateThrottle):
    """Limit unauthenticated payment event hints before provider verification."""

    scope = "toss_webhook"
    rate = "60/minute"

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }
