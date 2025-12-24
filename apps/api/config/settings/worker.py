# apps/api/config/settings/worker.py

from .base import *

# ⛔️ Worker는 URLConf를 타지 않도록
ROOT_URLCONF = None

# ==================================================
# Celery (Redis)
# ==================================================
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND")


CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"

# ==================================================
# Worker internal API call
# ==================================================
API_BASE_URL = os.getenv("API_BASE_URL")

INTERNAL_WORKER_TOKEN = "long-random-secret"
