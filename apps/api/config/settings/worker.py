# apps/api/config/settings/worker.py

INSTALLED_APPS = [
    # Django 최소
    "django.contrib.contenttypes",
    "django.contrib.auth",

    # 실제 존재하는 것만
    "apps.worker",

    # Celery 결과 백엔드 쓰면만
    "django_celery_results",
]
