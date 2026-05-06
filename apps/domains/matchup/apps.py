from django.apps import AppConfig


class MatchupConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.domains.matchup"
    verbose_name = "매치업"

    def ready(self):
        from . import signals  # noqa: F401
