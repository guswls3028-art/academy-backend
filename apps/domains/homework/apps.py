# PATH: apps/domains/homework/apps.py
# 역할: homework 도메인 앱 설정(AppConfig)

from django.apps import AppConfig


class HomeworkConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.domains.homework"
    label = "homework"
