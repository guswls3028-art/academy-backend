import os
from django.core.wsgi import get_wsgi_application
from django.urls import get_resolver

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "apps.api.config.settings.base",
)

application = get_wsgi_application()

# Load route modules before a fresh Gunicorn worker accepts its first request.
# Import failures must fail startup, not an otherwise healthy user's request.
_ = get_resolver().url_patterns
