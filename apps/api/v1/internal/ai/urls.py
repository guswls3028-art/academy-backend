#apps/api/v1/internal/ai/urls.py

from django.urls import path
from .views import next_ai_job_view, submit_ai_result_view

urlpatterns = [
    path("job/next/", next_ai_job_view, name="ai-job-next"),
    path("job/result/", submit_ai_result_view, name="ai-job-result"),
]
