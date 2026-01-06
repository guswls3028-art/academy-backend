# apps/domains/student_app/apps.py
from django.apps import AppConfig


class StudentAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.domains.student_app"
    label = "student_app"
