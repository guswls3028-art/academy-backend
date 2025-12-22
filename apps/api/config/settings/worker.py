# apps/api/config/settings/worker.py

INSTALLED_APPS = [
    # Django 최소
    "django.contrib.contenttypes",
    "django.contrib.auth",

    # 실제 존재하는 것만
    "apps.worker",

]
