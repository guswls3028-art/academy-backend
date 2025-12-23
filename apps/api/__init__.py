import os

if os.environ.get("DJANGO_SETTINGS_MODULE", "").endswith("settings.base"):
    from .celery import app as celery_app
