# PATH: apps/api/v1/internal/ai/urls.py
from django.urls import path

from .views import next_ai_job_view, submit_ai_result_view

urlpatterns = [
    path("next/", next_ai_job_view, name="internal-ai-next"),
    path("submit/", submit_ai_result_view, name="internal-ai-submit"),
]
