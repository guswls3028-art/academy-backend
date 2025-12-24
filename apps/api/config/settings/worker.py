# apps/api/config/settings/worker.py

from .base import *

# ⛔️ Worker는 URLConf를 타지 않도록
ROOT_URLCONF = None

# ==================================================
# Celery (Redis)
# ==================================================
CELERY_BROKER_URL = "redis://172.31.32.109:6379/0"
CELERY_RESULT_BACKEND = "redis://172.31.32.109:6379/0"

CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"

# ==================================================
# Worker internal API call
# ==================================================
API_BASE_URL = "http://localhost:8000"
INTERNAL_WORKER_TOKEN = "long-random-secret"
