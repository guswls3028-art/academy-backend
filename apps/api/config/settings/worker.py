# apps/api/config/settings/worker.py

from .base import *
import os

DEBUG = False

# 워커는 URL / admin / static 불필요
ROOT_URLCONF = None
WSGI_APPLICATION = None
ASGI_APPLICATION = None

# ==================================================
# Celery 제거됨 (SQS 기반 아키텍처로 전환)
# ==================================================

# ==================================================
# Worker → API 통신
# ==================================================

API_BASE_URL = os.environ["API_BASE_URL"]
INTERNAL_WORKER_TOKEN = os.environ.get(
    "INTERNAL_WORKER_TOKEN", "long-random-secret"
)
