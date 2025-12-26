# apps/worker/celery.py

print("🔥 WORKER CELERY LOADED 🔥")

import os
import django               # ✅ 추가
from celery import Celery

# Worker 전용 settings
os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "apps.api.config.settings.worker",
)

django.setup()               # ✅ 핵심 (이게 없어서 다 터졌음)

# 프로젝트 이름
app = Celery("academy")

# Django settings 로드
app.config_from_object(
    "django.conf:settings",
    namespace="CELERY",
)

# 🔥 추가 (이 줄이 핵심)
app.conf.broker_url = os.environ.get("CELERY_BROKER_URL")
app.conf.result_backend = os.environ.get("CELERY_RESULT_BACKEND")


# ✅ 핵심: 인자 없이 autodiscover
app.autodiscover_tasks()

print("🔥 autodiscover_tasks called 🔥")
