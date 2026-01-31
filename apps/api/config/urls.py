# apps/api/config/urls.py

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)

# ✅ [ADD] AI internal endpoint
from apps.domains.ai.views_internal import next_ai_job

urlpatterns = [
    # =========================
    # Admin
    # =========================
    path("admin/", admin.site.urls),

    # =========================
    # Auth (JWT)
    # =========================
    path("api/v1/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/v1/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),

    # =========================
    # API v1 (기존 구조 유지)
    # =========================
    path("api/v1/", include("apps.api.v1.urls")),

    # ==================================================
    # ✅ INTERNAL (ROOT LEVEL)
    # - Video Worker 전용
    # - 워커가 /internal/video-worker/* 직접 호출함
    # ==================================================
    path(
        "internal/",
        include("apps.support.video.urls_internal"),
    ),

    # ==================================================
    # ✅ INTERNAL AI WORKER (ADD ONLY)
    # ==================================================
    path(
        "api/v1/internal/ai/job/next/",
        next_ai_job,
    ),
]

# =========================
# DEV ONLY: media static
# =========================
if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )
