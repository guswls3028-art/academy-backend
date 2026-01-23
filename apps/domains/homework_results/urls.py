# PATH: apps/domains/homework_results/urls.py

from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.domains.homework_results.views.homework_view import HomeworkViewSet

router = DefaultRouter()
router.register("", HomeworkViewSet, basename="homeworks")  # ✅ 핵심

urlpatterns = [
    path("", include(router.urls)),
]
