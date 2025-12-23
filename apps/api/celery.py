# apps/api/celery.py

from celery import Celery

# ❗ settings는 여기서 지정하지 않는다
# DJANGO_SETTINGS_MODULE은 반드시 외부에서 주입

app = Celery("academy")

app.config_from_object(
    "django.conf:settings",
    namespace="CELERY",
)

# ✅ Django INSTALLED_APPS 기준으로 자동 탐색
app.autodiscover_tasks()
