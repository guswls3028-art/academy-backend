# apps/api/config/settings/dev.py

from .base import *

DEBUG = True
ALLOWED_HOSTS = ["*"]

# debug_toolbar — 2026-05-13: conf/urls.py 에 djdt URL include 추가했으므로 정상 활성화.
INSTALLED_APPS += [
    "debug_toolbar",
]
MIDDLEWARE.insert(0, "debug_toolbar.middleware.DebugToolbarMiddleware")

INTERNAL_IPS = ["127.0.0.1"]

