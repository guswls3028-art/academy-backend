# apps/domains/progress/apps.py
from django.apps import AppConfig


class ProgressConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.domains.progress"
    label = "progress"
