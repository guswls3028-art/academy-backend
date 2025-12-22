INSTALLED_APPS = [
    # Django 최소
    "django.contrib.contenttypes",
    "django.contrib.auth",

    # Celery task가 실제로 쓰는 것들만
    "apps.support.media",
    "apps.worker",

    # 필요 시
    "django_celery_results",
]
