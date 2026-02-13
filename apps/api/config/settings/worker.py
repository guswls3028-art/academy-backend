# ======================================================================
# PATH: apps/api/config/settings/worker.py
# ======================================================================
"""
Worker 전용 Django 설정 (API 의존성 없음)

절대 포함하지 않음:
  - corsheaders
  - rest_framework
  - django_extensions
  - admin (contrib.admin)
  - staticfiles
  - sessions (SessionMiddleware)
  - messages
  - TEMPLATES (템플릿 엔진)
"""
from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parents[3]

SECRET_KEY = os.getenv("SECRET_KEY", "worker-dev-secret")
DEBUG = False

# ==================================================
# URL / WSGI / ASGI — Worker는 HTTP 서버 아님
# ==================================================
ROOT_URLCONF = None
WSGI_APPLICATION = None
ASGI_APPLICATION = None

# ==================================================
# INSTALLED_APPS — 최소 (ORM만, API/Admin 제외)
# ==================================================
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "apps.core",  # api.common보다 먼저 (api.common가 core.models.base 의존)
    "apps.api.common",
    "apps.domains.students",
    "apps.domains.teachers",
    "apps.domains.parents",
    "apps.domains.staffs",
    "apps.domains.lectures",
    "apps.domains.enrollment",
    "apps.domains.attendance",
    "apps.domains.schedule",
    "apps.domains.community",
    "apps.domains.exams",
    "apps.domains.homework",
    "apps.domains.submissions",
    "apps.domains.results",
    "apps.domains.homework_results",
    "apps.domains.clinic",
    "apps.domains.progress",
    "apps.domains.ai.apps.AIDomainConfig",
    "apps.domains.assets",
    "apps.domains.student_app",
    "apps.support.video",
    "apps.support.messaging",
    "apps.shared",
]

# ==================================================
# MIDDLEWARE — 최소 (Security만)
# ==================================================
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

# ==================================================
# DATABASE
# ==================================================
AUTH_USER_MODEL = "core.User"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME"),
        "USER": os.getenv("DB_USER"),
        "PASSWORD": os.getenv("DB_PASSWORD"),
        "HOST": os.getenv("DB_HOST"),
        "PORT": os.getenv("DB_PORT", "5432"),
        "CONN_MAX_AGE": int(os.getenv("DB_CONN_MAX_AGE", "60")),
        "OPTIONS": {"connect_timeout": 10},
    }
}

# ==================================================
# GLOBAL
# ==================================================
LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ==================================================
# AWS / SQS
# ==================================================
AWS_REGION = os.getenv("AWS_REGION")
AWS_DEFAULT_REGION = os.getenv("AWS_DEFAULT_REGION", AWS_REGION)

VIDEO_SQS_QUEUE_NAME = os.getenv("VIDEO_SQS_QUEUE_NAME", "academy-video-jobs")
AI_SQS_QUEUE_NAME_LITE = os.getenv("AI_SQS_QUEUE_NAME_LITE", "academy-ai-jobs-lite")
AI_SQS_QUEUE_NAME_BASIC = os.getenv("AI_SQS_QUEUE_NAME_BASIC", "academy-ai-jobs-basic")
AI_SQS_QUEUE_NAME_PREMIUM = os.getenv("AI_SQS_QUEUE_NAME_PREMIUM", "academy-ai-jobs-premium")
MESSAGING_SQS_QUEUE_NAME = os.getenv("MESSAGING_SQS_QUEUE_NAME", "academy-messaging-jobs")

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
# Video / CDN
# ==================================================
CDN_HLS_BASE_URL = os.getenv("CDN_HLS_BASE_URL", "https://pub-54ae4dcb984d4491b08f6c57023a1621.r2.dev")

# ==================================================
# SOLAPI (Messaging Worker)
# ==================================================
SOLAPI_API_KEY = os.getenv("SOLAPI_API_KEY", "")
SOLAPI_API_SECRET = os.getenv("SOLAPI_API_SECRET", "")
SOLAPI_SENDER = os.getenv("SOLAPI_SENDER", "")
SOLAPI_KAKAO_PF_ID = os.getenv("SOLAPI_KAKAO_PF_ID", "")
SOLAPI_KAKAO_TEMPLATE_ID = os.getenv("SOLAPI_KAKAO_TEMPLATE_ID", "")

# ==================================================
# Worker → API 통신
# ==================================================
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
INTERNAL_WORKER_TOKEN = os.getenv("INTERNAL_WORKER_TOKEN", "long-random-secret")

# ==================================================
# ALLOWED_HOSTS (Django runserver용, Worker는 미사용)
# ==================================================
ALLOWED_HOSTS = ["*"]
