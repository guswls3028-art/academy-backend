# PATH: apps/domains/ai/urls.py
from django.urls import path

from apps.domains.ai.views.internal_ai_job_view import (
    InternalAIJobNextView,
    InternalAIJobResultView,
)

urlpatterns = [
    path("job/next/", InternalAIJobNextView.as_view(), name="internal-ai-job-next"),
    path("job/result/", InternalAIJobResultView.as_view(), name="internal-ai-job-result"),
]
