from django.apps import AppConfig


class LecturesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"

    # 🔥 Django 내부 경로
    name = "apps.domains.lectures"

    # 🔥 migration / FK / 참조용 앱 라벨 (절대 변경 금지)
    label = "lectures"
