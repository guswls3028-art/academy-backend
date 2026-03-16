# ======================================================================
# PATH: apps/api/config/settings/test.py
# CI smoke-test settings — no external dependencies (DB, AWS, Redis, R2)
# ======================================================================
from .base import *  # noqa: F401,F403

# ==================================================
# TEST MODE
# ==================================================

DEBUG = False
SECRET_KEY = "test-secret-key-not-for-production"

# ==================================================
# DATABASE — SQLite in-memory (no PostgreSQL needed)
# ==================================================

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# ==================================================
# ALLOWED HOSTS (test)
# ==================================================

ALLOWED_HOSTS = ["*"]

# ==================================================
# PASSWORD HASHERS — fast for tests
# ==================================================

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# ==================================================
# AWS / External Services — all disabled/mocked
# ==================================================

AWS_REGION = "us-east-1"
AWS_DEFAULT_REGION = "us-east-1"

# R2 — dummy values (no real connections)
R2_ACCESS_KEY = "test-access-key"
R2_SECRET_KEY = "test-secret-key"
R2_ENDPOINT = "https://test.r2.example.com"
R2_PUBLIC_BASE_URL = "https://test-public.r2.example.com"
R2_AI_BUCKET = "test-ai"
R2_VIDEO_BUCKET = "test-video"
R2_STORAGE_BUCKET = "test-storage"
R2_EXCEL_BUCKET = "test-excel"
R2_ADMIN_BUCKET = "test-admin"
R2_ADMIN_PUBLIC_BASE_URL = "https://test-admin-public.r2.example.com"

# SQS — dummy values
AI_SQS_QUEUE_NAME_LITE = "test-ai-queue"
AI_SQS_QUEUE_NAME_BASIC = "test-ai-queue"
AI_SQS_QUEUE_NAME_PREMIUM = "test-ai-queue"
MESSAGING_SQS_QUEUE_NAME = "test-messaging-queue"
VIDEO_SQS_QUEUE_DELETE_R2 = "test-video-delete-r2"

# Lambda / Internal
LAMBDA_INTERNAL_API_KEY = "test-lambda-key"
INTERNAL_API_ALLOW_IPS = ""
INTERNAL_WORKER_TOKEN = "test-worker-token"

# Worker instance IDs — None (no real instances)
AI_WORKER_INSTANCE_ID = None
VIDEO_WORKER_INSTANCE_ID = None

# Video worker
VIDEO_WORKER_MODE = "batch"

# Solapi — disabled
SOLAPI_API_KEY = ""
SOLAPI_API_SECRET = ""
SOLAPI_SENDER = ""
SOLAPI_KAKAO_PF_ID = ""
SOLAPI_KAKAO_TEMPLATE_ID = ""

# ==================================================
# THROTTLING — disabled for tests
# ==================================================

REST_FRAMEWORK = {
    **REST_FRAMEWORK,  # noqa: F405
    "DEFAULT_THROTTLE_CLASSES": [],
    "DEFAULT_THROTTLE_RATES": {},
}

# ==================================================
# LOGGING — minimal
# ==================================================

LOGGING = {
    "version": 1,
    "disable_existing_loggers": True,
    "handlers": {
        "null": {
            "class": "logging.NullHandler",
        }
    },
    "root": {
        "handlers": ["null"],
        "level": "CRITICAL",
    },
}
