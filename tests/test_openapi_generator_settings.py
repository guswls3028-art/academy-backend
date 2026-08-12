import os

from scripts.generate_openapi_schema import (
    SCHEMA_SETTINGS_MODULE,
    _configure_schema_settings,
)


def test_schema_generator_overrides_ambient_django_settings(monkeypatch):
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "apps.api.config.settings.test")

    _configure_schema_settings()

    assert SCHEMA_SETTINGS_MODULE == "apps.api.config.settings.schema"
    assert os.environ["DJANGO_SETTINGS_MODULE"] == SCHEMA_SETTINGS_MODULE
