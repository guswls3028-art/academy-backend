# ======================================================================
# PATH: apps/api/config/settings/prod.py
# ======================================================================
from .base import *
import os

# ==================================================
# PROD MODE
# ==================================================

DEBUG = False

# ==================================================
# SECURITY
# ==================================================

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# ==================================================
# ALLOWED HOSTS (Tenant == Host SSOT)
# ==================================================

ALLOWED_HOSTS = [
    "api.hakwonplus.com",
    "hakwonplus.com",
    "www.hakwonplus.com",
    "limglish.kr",
    "www.limglish.kr",
    "academy-frontend.pages.dev",
    # 로컬/EC2 내부 health check·ALB 타깃·Lambda backlog (Host: private IP)
    "localhost",
    "127.0.0.1",
    "172.30.3.142",
    ".ap-northeast-2.compute.internal",
    # ALB 직접 접근 (Cloudflare proxy 미설정 시 검증용)
    ".ap-northeast-2.elb.amazonaws.com",
]
# SSM env에서 추가 Host 허용 (쉼표 구분). 이미지 재빌드 없이 ALB 등 추가 가능.
_allowed_extra = os.environ.get("ALLOWED_HOSTS_EXTRA", "").strip()
if _allowed_extra:
    ALLOWED_HOSTS = list(ALLOWED_HOSTS) + [h.strip() for h in _allowed_extra.split(",") if h.strip()]

# ==================================================
# CORS
# ==================================================

CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOW_CREDENTIALS = True
# Cloudflare Pages *.pages.dev (프로젝트별 서브도메인)
CORS_ALLOWED_ORIGIN_REGEXES = [
    r"^https://[a-z0-9-]+\.pages\.dev$",
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
    "formatters": {
        "simple": {
            "format": "[{levelname}] {asctime} {name}: {message}",
            "style": "{",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
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
AI_WORKER_INSTANCE_ID = None
VIDEO_WORKER_INSTANCE_ID = None

# ==================================================
# FINAL ASSERTIONS
# ==================================================

assert DEBUG is False, "prod.py must run with DEBUG=False"
assert API_BASE_URL.startswith("https://"), "API_BASE_URL must be HTTPS"
