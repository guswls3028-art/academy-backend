# PostgreSQL-backed test settings for constraint/transaction/concurrency verification
# Usage: DJANGO_SETTINGS_MODULE=apps.api.config.settings.test_pg pytest ...

from .test import *  # noqa: F401,F403
import os

# Override SQLite with real PostgreSQL
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("TEST_DB_NAME", "test_academy_p0p1"),
        "USER": os.getenv("DB_USER", "admin97"),
        "PASSWORD": os.getenv("DB_PASSWORD"),
        "HOST": os.getenv("DB_HOST"),
        "PORT": os.getenv("DB_PORT", "5432"),
        "OPTIONS": {
            "connect_timeout": 10,
        },
        "TEST": {
            "NAME": "test_academy_p0p1",
        },
    }
}
