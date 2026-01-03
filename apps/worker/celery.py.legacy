# apps/worker/celery.py
import os
import django
from celery import Celery

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "apps.api.config.settings.worker",
)

django.setup()

app = Celery("academy")

app.config_from_object(
    "django.conf:settings",
    namespace="CELERY",
)

# 🔥 task 고정
app.autodiscover_tasks([
    "apps.shared.tasks",   # video
    "apps.worker.media",
])

print("🔥 Worker Celery READY (video / ai queues) 🔥")
