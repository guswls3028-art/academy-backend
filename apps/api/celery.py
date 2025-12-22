import os
from celery import Celery

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "apps.api.config.settings.base",
)

app = Celery("academy")

app.config_from_object("django.conf:settings", namespace="CELERY")

# 🔥 Single Source of Truth
app.autodiscover_tasks([
    "apps.shared.tasks",
])
