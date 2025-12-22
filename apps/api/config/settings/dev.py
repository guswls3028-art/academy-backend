from .base import *

DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS += [
    "debug_toolbar",
]

MIDDLEWARE.insert(0, "debug_toolbar.middleware.DebugToolbarMiddleware")

INTERNAL_IPS = [
    "127.0.0.1",
]

# 🔴 base 설정 유지 + 인증 방식만 추가
REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"] = [
    "rest_framework.authentication.SessionAuthentication",
    "rest_framework_simplejwt.authentication.JWTAuthentication",
]
