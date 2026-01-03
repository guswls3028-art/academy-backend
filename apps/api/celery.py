# apps/api/celery.py
import os
from celery import Celery

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "apps.api.config.settings.base",
)

app = Celery("academy")

app.config_from_object(
    "django.conf:settings",
    namespace="CELERY",
)

# 🔥🔥🔥 Celery 5.6 worker_state_db 강제 설정 (필수)
app.conf.worker_state_db = None

# task autodiscover
app.autodiscover_tasks([
    "apps.shared.tasks",
])

print("🔥 API Celery READY 🔥")
