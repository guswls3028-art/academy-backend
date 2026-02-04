# ======================================================================
# PATH: apps/api/config/settings/base.py
# ======================================================================
from pathlib import Path
from datetime import timedelta
import os
from corsheaders.defaults import default_headers  # 🔥 추가

# 나중에 빼
DEBUG_TOOLBAR_CONFIG = {"SHOW_TOOLBAR_CALLBACK": lambda request: False}

# ==================================================
# BASE
# ==================================================

BASE_DIR = Path(__file__).resolve().parents[3]

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
DEBUG = os.getenv("DEBUG", "true").lower() == "true"

# ==================================================
# 🔥 AWS / WORKER INSTANCE (SSOT)
# ==================================================

AWS_REGION = os.getenv("AWS_REGION")
AWS_DEFAULT_REGION = os.getenv("AWS_DEFAULT_REGION", AWS_REGION)

AI_WORKER_INSTANCE_ID = os.getenv("AI_WORKER_INSTANCE_ID")
VIDEO_WORKER_INSTANCE_ID = os.getenv("VIDEO_WORKER_INSTANCE_ID")

# ==================================================
# ✅ MULTI TENANT (SSOT)  --- Enterprise baseline
# ==================================================
TENANT_HEADER_NAME = os.getenv("TENANT_HEADER_NAME", "X-Tenant-Code")
TENANT_QUERY_PARAM_NAME = os.getenv("TENANT_QUERY_PARAM_NAME", "tenant")

# optional: dev/초기 운영에서 header 없이 기본 tenant 지정
TENANT_DEFAULT_CODE = None  # 🔥 풀백 제거

# strict: tenant 미지정 시 400 (멀티테넌트 운영에서는 True 권장)
TENANT_STRICT = os.getenv("TENANT_STRICT", "false").lower() == "true"

# inactive tenant 허용 여부 (운영에서는 False 유지)
TENANT_ALLOW_INACTIVE = os.getenv("TENANT_ALLOW_INACTIVE", "false").lower() == "true"

# 테넌트 없어도 되는 prefix
TENANT_BYPASS_PATH_PREFIXES = [
    "/admin/",
    "/api/v1/token/",
    "/api/v1/token/refresh/",
    "/internal/",
    "/api/v1/internal/",
    "/swagger",
    "/redoc",
]

# ==================================================
# ALLOWED HOSTS
# ==================================================
ALLOWED_HOSTS = [
    "127.0.0.1",
    "localhost",
    "13.125.207.197",
    "172.31.32.253",
    "172.31.32.109",
    "hakwonplus.com",
    "www.hakwonplus.com",
    "api.hakwonplus.com",
    "limglish.kr",
    ".limglish.kr",
    "academy-frontend.pages.dev",
]

AUTH_USER_MODEL = "core.User"

# ==================================================
# INSTALLED APPS
# ==================================================
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.api.common",
    "apps.core",
    "apps.domains.students",
    "apps.domains.teachers",
    "apps.domains.staffs",
    "apps.domains.parents",
    "apps.domains.lectures",
    "apps.domains.enrollment",
    "apps.domains.attendance",
    "apps.domains.schedule",
    "apps.domains.interactions.materials",
    "apps.domains.interactions.questions",
    "apps.domains.interactions.counseling",
    "apps.domains.interactions.boards",
    "apps.domains.exams",
    "apps.domains.homework",
    "apps.domains.submissions",
    "apps.domains.results",
    "apps.domains.homework_results",
    "apps.domains.clinic",
    "apps.domains.progress",
    "apps.domains.ai.apps.AIDomainConfig",
    "apps.domains.assets",
    "apps.support.video",
    "rest_framework",
    "rest_framework_simplejwt",
    "django_filters",
    "drf_yasg",
    "corsheaders",
    "apps.shared",
    "django_extensions",
    "apps.domains.student_app",
]

# ==================================================
# MIDDLEWARE
# ==================================================
MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "apps.core.middleware.tenant.TenantMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# ==================================================
# URL / WSGI / ASGI
# ==================================================
ROOT_URLCONF = "apps.api.config.urls"
WSGI_APPLICATION = "apps.api.config.wsgi.application"
ASGI_APPLICATION = "apps.api.config.asgi.application"

# ==================================================
# DATABASE
# ==================================================
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME"),
        "USER": os.getenv("DB_USER"),
        "PASSWORD": os.getenv("DB_PASSWORD"),
        "HOST": os.getenv("DB_HOST"),
        "PORT": os.getenv("DB_PORT", "5432"),
    }
}

# ==================================================
# GLOBAL
# ==================================================
LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "storage" / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ==================================================
# DRF
# ==================================================
REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework_simplejwt.authentication.JWTAuthentication"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
}

# ==================================================
# JWT
# ==================================================
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(days=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=60),
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# ==================================================
# CORS
# ==================================================
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "https://hakwonplus.com",
    "https://www.hakwonplus.com",
    "https://academy-frontend.pages.dev",
    "https://limglish.kr",
    "https://www.limglish.kr",
]
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = [
    "https://hakwonplus.com",
    "https://www.hakwonplus.com",
    "https://academy-frontend.pages.dev",
]

# 🔥 추가: tenant header 허용
CORS_ALLOW_HEADERS = list(default_headers) + ["X-Tenant-Code"]

# ==================================================
# VIDEO / CDN
# ==================================================
CDN_HLS_BASE_URL = "https://pub-54ae4dcb984d4491b08f6c57023a1621.r2.dev"
VIDEO_PLAYBACK_TTL_SECONDS = int(os.getenv("VIDEO_PLAYBACK_TTL_SECONDS", "600"))

# ==================================================
# Cloudflare R2
# ==================================================
R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY")
R2_SECRET_KEY = os.getenv("R2_SECRET_KEY")
R2_ENDPOINT = os.getenv("R2_ENDPOINT")
R2_PUBLIC_BASE_URL = os.getenv("R2_PUBLIC_BASE_URL")
R2_AI_BUCKET = os.getenv("R2_AI_BUCKET", "academy-ai")
R2_VIDEO_BUCKET = os.getenv("R2_VIDEO_BUCKET", "academy-video")

# ==================================================
# TEMPLATES
# ==================================================
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ==================================================
# REDIS
# ==================================================
REDIS_URL = os.getenv("REDIS_URL")

# ==================================================
# INTERNAL WORKER (FIX)
# ==================================================
INTERNAL_WORKER_TOKEN = os.getenv("INTERNAL_WORKER_TOKEN", "")
