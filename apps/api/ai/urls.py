# apps/api/v1/internal/ai/urls.py

from django.urls import path, include

urlpatterns = [
    # ✅ AI Domain 단일 SSOT(DBQueue) 라우팅
    # - AI Worker(run.py)가 호출하는 endpoint를 그대로 유지:
    #   /api/v1/internal/ai/job/next/
    #   /api/v1/internal/ai/job/result/
    #
    # - 구현은 apps.domains.ai.urls (InternalAIJobNextView / InternalAIJobResultView)
    path("", include("apps.domains.ai.urls")),
]
