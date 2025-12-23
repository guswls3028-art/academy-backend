# apps/worker/celery.py


print("🔥 WORKER CELERY LOADED 🔥")



import os
from celery import Celery

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "apps.api.config.settings.worker",
)

app = Celery("worker")

app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks([
    "apps.shared.tasks.media",
])

print("🔥 autodiscover_tasks called 🔥")