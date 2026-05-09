# apps/domains/submissions/apps.py
from django.apps import AppConfig


class SubmissionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.domains.submissions"
    label = "submissions"

    def ready(self):
        # signal 핸들러 등록 (Exam/Homework 삭제 시 active submission auto-discard)
        from . import signals  # noqa: F401
