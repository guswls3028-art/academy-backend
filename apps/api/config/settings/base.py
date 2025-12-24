# apps\api\config\setttings\base.py

from pathlib import Path
from datetime import timedelta
import os

# ==================================================
# BASE
# ==================================================

BASE_DIR = Path(__file__).resolve().parents[3]

SECRET_KEY = "dev-secret-key"
DEBUG = True
ALLOWED_HOSTS = ["*"]

AUTH_USER_MODEL = "core.User"

API_BASE_URL = "http://localhost:8000"



# ==================================================
# INSTALLED APPS
# ==================================================

INSTALLED_APPS = [
    # Django
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # Common / Core
    "apps.api.common",
    "apps.core",

    # Domain Apps
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

    "apps.domains.clinic",

    "apps.domains.ai.apps.AIDomainConfig",

    # support
    "apps.support.media",

    # REST
    "rest_framework",
    "rest_framework_simplejwt",
    "django_filters",

    # Swagger
    "drf_yasg",

    # CORS
    "corsheaders",

    # shared 여기에 등록해야 워커에서 줏어감. 
    "apps.shared",
]

# ==================================================
# MIDDLEWARE
# ==================================================

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",

    # Tenant
    "apps.core.middleware.tenant.TenantMiddleware",
]

# ==================================================
# URL / WSGI / ASGI
# ==================================================

ROOT_URLCONF = "apps.api.config.urls"

WSGI_APPLICATION = "apps.api.config.wsgi.application"
ASGI_APPLICATION = "apps.api.config.asgi.application"

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
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

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
# AUTH
# ==================================================

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
]

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"
    },
]

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
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
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

CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_CREDENTIALS = True

# ==================================================
# CELERY / REDIS
# ==================================================

CELERY_BROKER_URL = "redis://172.31.32.109:6379/0"
CELERY_RESULT_BACKEND = "redis://172.31.32.109:6379/1"

CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"

CELERY_TASK_DEFAULT_QUEUE = "default"

CELERY_TIMEZONE = TIME_ZONE

CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

INTERNAL_WORKER_TOKEN = "long-random-secret"

# ==================================================
# VIDEO PLAYBACK / CDN
# ==================================================

CLOUDFRONT_DOMAIN = os.getenv("CLOUDFRONT_DOMAIN", "")
CLOUDFRONT_KEY_PAIR_ID = os.getenv("CLOUDFRONT_KEY_PAIR_ID", "")
CLOUDFRONT_PRIVATE_KEY_PEM = os.getenv("CLOUDFRONT_PRIVATE_KEY_PEM", "")

# ✅ 수정된 부분
CDN_HLS_BASE_URL = "https://pub-54ae4dcb984d4491b08f6c57023a1621.r2.dev"

VIDEO_PLAYBACK_TTL_SECONDS = int(os.getenv("VIDEO_PLAYBACK_TTL_SECONDS", "600"))
VIDEO_MAX_SESSIONS = int(os.getenv("VIDEO_MAX_SESSIONS", "9999"))
VIDEO_MAX_DEVICES = int(os.getenv("VIDEO_MAX_DEVICES", "9999"))

REQUIRED_ENV_VARS = [
    ("CLOUDFRONT_DOMAIN", CLOUDFRONT_DOMAIN),
    ("CLOUDFRONT_KEY_PAIR_ID", CLOUDFRONT_KEY_PAIR_ID),
    ("CLOUDFRONT_PRIVATE_KEY_PEM", CLOUDFRONT_PRIVATE_KEY_PEM),
    ("CDN_HLS_BASE_URL", CDN_HLS_BASE_URL),
]

# NOTE:
# 개발/베타 단계에서는 예외를 던지지 않고 상태만 기록한다.
# 실제 재생 API에서만 VIDEO_PLAYBACK_ENABLED 여부를 확인해 차단한다.
VIDEO_PLAYBACK_CONFIG_MISSING = [
    name for name, value in REQUIRED_ENV_VARS if not value
]

VIDEO_PLAYBACK_ENABLED = not bool(VIDEO_PLAYBACK_CONFIG_MISSING)





# ------------------------------------------------------------------
# Cloudflare R2
# ------------------------------------------------------------------
R2_ACCESS_KEY = os.environ.get("R2_ACCESS_KEY")
R2_SECRET_KEY = os.environ.get("R2_SECRET_KEY")
R2_ENDPOINT = os.environ.get("R2_ENDPOINT")
R2_PUBLIC_BASE_URL = os.environ.get("R2_PUBLIC_BASE_URL")
R2_BUCKET = os.environ.get("R2_BUCKET")

# 안전장치 (개발 중에만)
if DEBUG:
    print("[settings] R2_ENDPOINT =", R2_ENDPOINT)