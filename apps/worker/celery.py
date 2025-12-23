# apps/worker/celery.py

print("🔥 WORKER CELERY LOADED 🔥")

import os
from celery import Celery

# Worker 전용 settings
os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "apps.api.config.settings.worker",
)

# 프로젝트 이름
app = Celery("academy")

# Django settings 로드
app.config_from_object(
    "django.conf:settings",
    namespace="CELERY",
)

# ✅ 핵심: 인자 없이 autodiscover
app.autodiscover_tasks()

print("🔥 autodiscover_tasks called 🔥")
