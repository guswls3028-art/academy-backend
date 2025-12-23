# apps/api/config/settings/worker.py

from .base import *

# ==================================================
# Celery (Redis)
# ==================================================
CELERY_BROKER_URL = "redis://localhost:6379/0"
CELERY_RESULT_BACKEND = "redis://localhost:6379/1"

CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"

# ==================================================
# Worker internal API call
# ==================================================
API_BASE_URL = "http://localhost:8000"
INTERNAL_WORKER_TOKEN = "long-random-secret"
