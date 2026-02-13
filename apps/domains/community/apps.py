from django.apps import AppConfig


class CommunityConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.domains.community"
    verbose_name = "Community (SSOT)"

    def ready(self):
        import apps.domains.community.signals  # noqa: F401
