# apps/worker/celery.py
print("🔥 WORKER CELERY LOADED 🔥")

import os
import django
from celery import Celery

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "apps.api.config.settings.worker",
)

django.setup()

app = Celery("academy")

# ✅ Django settings만 신뢰
app.config_from_object(
    "django.conf:settings",
    namespace="CELERY",
)

app.autodiscover_tasks()

print("🔥 autodiscover_tasks called 🔥")
