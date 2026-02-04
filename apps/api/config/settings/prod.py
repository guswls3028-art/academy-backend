# ======================================================================
# PATH: apps/api/config/settings/prod.py
# ======================================================================
from .base import *
import os

# ==================================================
# PROD MODE (외부 공개 API 서버 기준)
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
# ALLOWED HOSTS (외부 계약 기준)
# ==================================================
ALLOWED_HOSTS = [
    "hakwonplus.com",
    "www.hakwonplus.com",
    "api.hakwonplus.com",
    "limglish.kr",
    "www.limglish.kr",
    "academy-frontend.pages.dev",
    "localhost",
    "127.0.0.1",
]

# ==================================================
# CORS
# ==================================================
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = [
    "https://hakwonplus.com",
    "https://www.hakwonplus.com",
    "https://academy-frontend.pages.dev",
    "http://localhost:5173",
    "https://limglish.kr",
    "https://www.limglish.kr",
]
CORS_ALLOW_CREDENTIALS = True

# 🔥 추가: tenant header 허용
from corsheaders.defaults import default_headers
CORS_ALLOW_HEADERS = list(default_headers) + ["X-Tenant-Code"]

# ==================================================
# CSRF
# ==================================================
CSRF_TRUSTED_ORIGINS = [
    "https://hakwonplus.com",
    "https://www.hakwonplus.com",
    "https://academy-frontend.pages.dev",
    "https://limglish.kr",
    "https://www.limglish.kr",
]

# ==================================================
# API BASE
# ==================================================
API_BASE_URL = "https://api.hakwonplus.com"

# ==================================================
# ✅ MULTI TENANT (PROD 운영 기준)
# ==================================================
TENANT_STRICT = True
TENANT_HEADER_NAME = os.environ.get("TENANT_HEADER_NAME", TENANT_HEADER_NAME)
TENANT_DEFAULT_CODE = None  # 🔥 풀백 제거

# ==================================================
# LOGGING
# ==================================================
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "[{levelname}] {asctime} {name}: {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}

# ==================================================
# STATIC / MEDIA
# ==================================================
STATICFILES_STORAGE = "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"

# ==================================================
# WORKER SAFETY GUARD
# ==================================================
INTERNAL_WORKER_TOKEN = os.environ.get("INTERNAL_WORKER_TOKEN", "")
AI_WORKER_INSTANCE_ID = None
VIDEO_WORKER_INSTANCE_ID = None

# ==================================================
# FINAL ASSERTIONS
# ==================================================
assert DEBUG is False, "prod.py must run with DEBUG=False"
assert API_BASE_URL.startswith("https://"), "API_BASE_URL must be external HTTPS URL"

# ==================================================
# REDIS
# ==================================================
REDIS_URL = os.getenv("REDIS_URL")
