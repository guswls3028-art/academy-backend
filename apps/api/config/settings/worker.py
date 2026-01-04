# apps/api/config/settings/worker.py

from .base import *
import os

DEBUG = False

# 워커는 URL / admin / static 불필요
ROOT_URLCONF = None
WSGI_APPLICATION = None
ASGI_APPLICATION = None

# ==================================================
# Celery
# ==================================================

CELERY_BROKER_URL = os.environ["CELERY_BROKER_URL"]
CELERY_RESULT_BACKEND = os.environ["CELERY_RESULT_BACKEND"]

CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"

CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

# 🔥 Celery 5.6 핵심 패치
CELERY_WORKER_STATE_DB = None
worker_state_db = None

# ==================================================
# Worker → API 통신
# ==================================================

API_BASE_URL = os.environ["API_BASE_URL"]
INTERNAL_WORKER_TOKEN = os.environ.get(
    "INTERNAL_WORKER_TOKEN", "long-random-secret"
)
