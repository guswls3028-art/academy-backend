# PATH: apps/domains/homework_results/urls.py

from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.domains.homework_results.views.homework_view import HomeworkViewSet
from apps.domains.homework_results.views.homework_template_with_usage import HomeworkTemplateWithUsageListView
from apps.domains.homework_results.views.homework_save_as_template_view import HomeworkSaveAsTemplateView

router = DefaultRouter()
router.register("", HomeworkViewSet, basename="homeworks")

urlpatterns = [
    path("templates/with-usage/", HomeworkTemplateWithUsageListView.as_view()),
    path("<int:homework_id>/save-as-template/", HomeworkSaveAsTemplateView.as_view()),
    path("", include(router.urls)),
]
