# PATH: apps/api/config/settings/prod.py

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
# ⚠️ base.py의 ALLOWED_HOSTS를 그대로 확장/축소하지 않음
# 단, prod에서는 "*" 절대 금지

ALLOWED_HOSTS = [
    # =========================
    # Domains
    # =========================
    "hakwonplus.com",
    "www.hakwonplus.com",
    "api.hakwonplus.com",

    # =========================
    # Cloudflare Pages (frontend)
    # =========================
    "academy-frontend.pages.dev",

    # =========================
    # Local dev (optional, safe)
    # =========================
    "localhost",
    "127.0.0.1",
]

# ==================================================
# CORS (Frontend ↔ API 계약)
# ==================================================

CORS_ALLOW_ALL_ORIGINS = False

CORS_ALLOWED_ORIGINS = [
    "https://hakwonplus.com",
    "https://www.hakwonplus.com",
    "https://academy-frontend.pages.dev",
    "http://localhost:5173",  # local dev
]

CORS_ALLOW_CREDENTIALS = True

# ==================================================
# CSRF
# ==================================================

CSRF_TRUSTED_ORIGINS = [
    "https://hakwonplus.com",
    "https://www.hakwonplus.com",
    "https://academy-frontend.pages.dev",
]

# ==================================================
# API BASE (🔥 중요)
# ==================================================
# ❌ 내부 IP 사용 금지
# ❌ worker용 API_BASE_URL 혼입 금지
# ✅ 외부 공개 기준 URL만 사용

API_BASE_URL = "https://api.hakwonplus.com"

# ==================================================
# LOGGING (운영 최소 기준)
# ==================================================

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {
            "format": "[{levelname}] {asctime} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}

# ==================================================
# STATIC / MEDIA
# ==================================================
# gunicorn + nginx + CDN 전제
# Django는 서빙 책임 없음

STATICFILES_STORAGE = "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"

# ==================================================
# WORKER SAFETY GUARD
# ==================================================
# prod API 서버에서는 worker 전용 설정을 신뢰하지 않음
# (있어도 사용 안 함)

INTERNAL_WORKER_TOKEN = os.environ.get("INTERNAL_WORKER_TOKEN", "")
AI_WORKER_INSTANCE_ID = None
VIDEO_WORKER_INSTANCE_ID = None

# ==================================================
# FINAL ASSERTIONS (운영 안정성)
# ==================================================

assert DEBUG is False, "prod.py must run with DEBUG=False"
assert API_BASE_URL.startswith("https://"), "API_BASE_URL must be external HTTPS URL"

# ==================================================
# REDIS 레가시 버그 방지
# ==================================================

REDIS_URL = os.getenv("REDIS_URL")