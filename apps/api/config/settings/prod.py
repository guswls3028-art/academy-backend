# ======================================================================
# PATH: apps/api/config/settings/prod.py
# ======================================================================
from .base import *
import os

# The public API is reached through the Academy ALB in the canonical VPC.
# Only this private proxy range may contribute X-Forwarded-For hops.
if not TRUSTED_PROXY_CIDRS:
    TRUSTED_PROXY_CIDRS = "172.30.0.0/16"

# ==================================================
# PROD MODE
# ==================================================

DEBUG = False

# ==================================================
# SECURITY
# ==================================================

def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
# HTTPS is enforced at the edge/ALB. Keeping Django's redirect enabled by
# default can create a self-redirect loop when proxy scheme metadata is not
# preserved, which blocks API clients before auth/tenant code runs.
SECURE_SSL_REDIRECT = _env_bool("DJANGO_SECURE_SSL_REDIRECT", False)
SECURE_REDIRECT_EXEMPT = [
    r"^health/?$",
    r"^healthz/?$",
    r"^readyz/?$",
]
SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_SECURE_HSTS_SECONDS", "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", True)
SECURE_HSTS_PRELOAD = _env_bool("DJANGO_SECURE_HSTS_PRELOAD", True)

# ==================================================
# ALLOWED HOSTS (Tenant == Host SSOT)
# ==================================================
# ALB 타깃 헬스체크는 Host: 타깃 private IP 로 요청함. 172.30.0.0/22 허용.
_vpc_hosts = [f"172.30.{a}.{b}" for a in range(4) for b in range(256)]

ALLOWED_HOSTS = [
    "api.hakwonplus.com",
    "hakwonplus.com",
    "www.hakwonplus.com",
    "limglish.kr",
    "www.limglish.kr",
    "tchul.com",
    "www.tchul.com",
    "ymath.co.kr",
    "www.ymath.co.kr",
    "sswe.co.kr",
    "www.sswe.co.kr",
    "dnbacademy.co.kr",
    "www.dnbacademy.co.kr",
    "academy-frontend.pages.dev",
    # 로컬/EC2 내부 health check·ALB 타깃·Lambda backlog (Host: private IP)
    "localhost",
    "127.0.0.1",
    *(_vpc_hosts),
    ".ap-northeast-2.compute.internal",
    # ALB 직접 접근 (Cloudflare proxy 미설정 시 검증용)
    ".ap-northeast-2.elb.amazonaws.com",
]

# ==================================================
# CORS
# ==================================================

CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOW_CREDENTIALS = True
# Cloudflare Pages *.pages.dev (프로젝트별 서브도메인)
CORS_ALLOWED_ORIGIN_REGEXES = [
    r"^https://[a-z0-9-]*academy-frontend[a-z0-9-]*\.pages\.dev$",
]

CORS_ALLOWED_ORIGINS = [
    "https://hakwonplus.com",
    "https://www.hakwonplus.com",
    "https://academy-frontend.pages.dev",
    "https://limglish.kr",
    "https://www.limglish.kr",
    "https://tchul.com",
    "https://www.tchul.com",
    "https://ymath.co.kr",
    "https://www.ymath.co.kr",
    "https://sswe.co.kr",
    "https://www.sswe.co.kr",
    "https://dnbacademy.co.kr",
    "https://www.dnbacademy.co.kr",
    "https://dev-web.hakwonplus.com",
    # 로컬 개발용 (프론트엔드 localhost:5174에서 배포된 API 서버 사용)
    "http://localhost:5174",
    # Vite preview 기본 포트
    "http://localhost:4173",
]

try:
    from corsheaders.defaults import default_headers
except ImportError:
    default_headers = []
CORS_ALLOW_HEADERS = list(default_headers) + [
    "X-Client-Version",
    "X-Client",
    "X-Tenant-Code",
    "X-Student-Id",
]

# ==================================================
# CSRF (PROD STRICT) ✅
# ==================================================

CSRF_TRUSTED_ORIGINS = [
    "https://hakwonplus.com",
    "https://www.hakwonplus.com",
    "https://academy-frontend.pages.dev",
    "https://limglish.kr",
    "https://www.limglish.kr",
    "https://tchul.com",
    "https://www.tchul.com",
    "https://ymath.co.kr",
    "https://www.ymath.co.kr",
    "https://sswe.co.kr",
    "https://www.sswe.co.kr",
    "https://dnbacademy.co.kr",
    "https://www.dnbacademy.co.kr",
]

# ==================================================
# API BASE
# ==================================================

API_BASE_URL = "https://api.hakwonplus.com"

# ==================================================
# TENANT (Host-only, strict by definition)
# ==================================================

TENANT_HEADER_NAME = None
TENANT_QUERY_PARAM_NAME = None
TENANT_DEFAULT_CODE = None
TENANT_STRICT = None
TENANT_ALLOW_INACTIVE = None

# ==================================================
# LOGGING
# ==================================================

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "correlation_id": {
            "()": "apps.api.common.correlation.CorrelationIdFilter",
        },
    },
    "formatters": {
        "json": {
            "()": "apps.api.common.logging_json.JsonFormatter",
        },
        "simple": {
            "format": "[{levelname}] {asctime} [{correlation_id}] {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "filters": ["correlation_id"],
        }
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}

# ==================================================
# STATIC / MEDIA
# ==================================================

STATICFILES_STORAGE = "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"

# ==================================================
# INTERNAL WORKER SAFETY
# ==================================================

INTERNAL_WORKER_TOKEN = os.environ.get("INTERNAL_WORKER_TOKEN", "")
# AI_WORKER_INSTANCE_ID: base.py에서 os.getenv()로 읽음 — SSM에 설정된 값 사용
# VIDEO_WORKER_INSTANCE_ID: 현재 Batch 방식이므로 미사용

# ==================================================
# FINAL ASSERTIONS
# ==================================================

assert DEBUG is False, "prod.py must run with DEBUG=False"
assert API_BASE_URL.startswith("https://"), "API_BASE_URL must be HTTPS"
# SECRET_KEY 강제: env 미설정 또는 base.py dev 기본값 fallthrough 차단 (JWT 서명/세션 보호).
# SSM 주입이 실패한 채 기동되면 fail-closed.
_secret_env = os.getenv("SECRET_KEY", "")
if not _secret_env or _secret_env == "dev-secret-key" or len(_secret_env) < 32:
    from django.core.exceptions import ImproperlyConfigured
    raise ImproperlyConfigured(
        "SECRET_KEY must be set via SSM/env (>=32 chars, not the dev default) in production."
    )
_messaging_binding_key = os.getenv("MESSAGING_TENANT_BINDING_KEY", "").strip()
if len(_messaging_binding_key) < 32:
    from django.core.exceptions import ImproperlyConfigured
    raise ImproperlyConfigured(
        "MESSAGING_TENANT_BINDING_KEY must be set via SSM/env (>=32 chars) in production."
    )
if BILLING_KEY_ENCRYPTION_WRITE_ENABLED and not BILLING_KEY_ENCRYPTION_PRIMARY_KEY:
    from django.core.exceptions import ImproperlyConfigured
    raise ImproperlyConfigured(
        "BILLING_KEY_ENCRYPTION_PRIMARY_KEY is required when encrypted billing-key writes are enabled."
    )
_billing_encryption_keys = [
    BILLING_KEY_ENCRYPTION_PRIMARY_KEY,
    *BILLING_KEY_ENCRYPTION_FALLBACK_KEYS,
]
if any(_billing_encryption_keys):
    try:
        from cryptography.fernet import Fernet

        for _billing_encryption_key in _billing_encryption_keys:
            if _billing_encryption_key:
                Fernet(_billing_encryption_key.encode("ascii"))
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        from django.core.exceptions import ImproperlyConfigured

        raise ImproperlyConfigured(
            "Billing-key encryption keyring contains an invalid Fernet key."
        ) from exc
