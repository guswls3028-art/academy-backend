# apps/api/celery.py

import os
from celery import Celery

app = Celery("academy_api")

app.config_from_object(
    "django.conf:settings",
    namespace="CELERY",
)

app.conf.worker_state_db = None

# 🔒 API에서 필요한 것만
app.autodiscover_tasks([
    "apps.domains.progress.tasks",
    "apps.domains.results.tasks",
])

print("🔥 API Celery READY 🔥")

