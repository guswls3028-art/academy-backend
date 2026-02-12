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
]

# ==================================================
# CORS
# ==================================================

CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOW_CREDENTIALS = True

CORS_ALLOWED_ORIGINS = [
    "https://hakwonplus.com",
    "https://www.hakwonplus.com",
    "https://academy-frontend.pages.dev",
    "https://limglish.kr",
    "https://www.limglish.kr",
    "https://dev-web.hakwonplus.com",
]

from corsheaders.defaults import default_headers
CORS_ALLOW_HEADERS = list(default_headers) + [
    "X-Client-Version",
    "X-Client",
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
