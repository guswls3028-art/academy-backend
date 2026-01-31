# apps/domains/ai/apps.py
from django.apps import AppConfig


class AIDomainConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.domains.ai"
    label = "ai_domain"
