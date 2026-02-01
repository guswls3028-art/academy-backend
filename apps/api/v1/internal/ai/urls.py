# ====================================================================================================
# FILE: apps/api/v1/internal/ai/urls.py
# ====================================================================================================
from django.urls import path, include

urlpatterns = [
    # AI Domain SSOT forwarding
    # Worker endpoints:
    #   GET  /api/v1/internal/ai/job/next/
    #   POST /api/v1/internal/ai/job/result/
    path("", include("apps.domains.ai.urls")),
]
