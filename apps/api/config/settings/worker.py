# apps/api/config/settings/worker.py

from pathlib import Path
from .base import *

# =========================
# Celery (Redis)
# =========================
CELERY_BROKER_URL = "redis://localhost:6379/0"
CELERY_RESULT_BACKEND = "redis://localhost:6379/1"

CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"

BASE_DIR = Path(__file__).resolve().parents[4]

SECRET_KEY = "worker-only"

DEBUG = False

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",

    "apps.shared",
    "apps.worker",
    "apps.support.media",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "worker.sqlite3",
    }
}

CELERY_BROKER_URL = "redis://localhost:6379/0"
CELERY_RESULT_BACKEND = "redis://localhost:6379/1"

CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"

API_BASE_URL = "http://localhost:8000"
INTERNAL_WORKER_TOKEN = "long-random-secret"
