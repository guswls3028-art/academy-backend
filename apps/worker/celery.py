# apps/worker/celery.py

import os
from celery import Celery

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "apps.api.config.settings.worker",
)

app = Celery("worker")

app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks([
    "apps.shared.tasks",
])
