# apps/api/celery.py

import os
from celery import Celery

# ⚠️ settings는 외부에서 주입 (API / Worker 분리)
# os.environ.setdefault(...) ❌ 절대 쓰지 말 것

app = Celery("academy")

app.config_from_object(
    "django.conf:settings",
    namespace="CELERY",
)

# Celery 5.6 worker_state_db 이슈 회피
app.conf.worker_state_db = None

# task autodiscover
app.autodiscover_tasks([
    "apps.shared.tasks",
])

print("🔥 API Celery READY 🔥")
