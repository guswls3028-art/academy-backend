# apps/worker/celery.py

print("🔥 WORKER CELERY LOADED 🔥")

import os
from celery import Celery

# ✅ Worker 전용 settings 사용
os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "apps.api.config.settings.worker",
)

# ✅ 프로젝트 이름 기준으로 Celery 앱 생성
app = Celery("academy")

# ✅ Django settings에서 CELERY_* 로딩
app.config_from_object(
    "django.conf:settings",
    namespace="CELERY",
)

# ✅ task autodiscover (앱 단위)
app.autodiscover_tasks([
    "apps.shared",
])

print("🔥 autodiscover_tasks called 🔥")
