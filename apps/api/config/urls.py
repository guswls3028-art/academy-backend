# apps/api/config/urls.py

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from rest_framework_simplejwt.views import TokenRefreshView
from apps.api.common.views import health_check
from apps.api.common.auth_jwt import TenantAwareTokenObtainPairView

urlpatterns = [
    # =========================
    # Health Check
    # =========================
    path("health", health_check, name="health_check"),
    
    # =========================
    # Admin
    # =========================
    path("admin/", admin.site.urls),

    # =========================
    # Auth (JWT)
    # =========================
    path("api/v1/token/", TenantAwareTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/v1/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),

    # =========================
    # DRF Browsable API 로그인 (우측 상단 Log in)
    # =========================
    path("api-auth/", include("rest_framework.urls")),

    # =========================
    # API v1 (기존 구조 유지)
    # =========================
    path("api/v1/", include("apps.api.v1.urls")),

    # ==================================================
    # ✅ INTERNAL (ROOT LEVEL)
    # - Video Worker HTTP polling 엔드포인트 제거됨 (SQS 기반으로 전환)
    # - Legacy 호환성을 위해 일부 엔드포인트는 유지
    # ==================================================
    # path(
    #     "internal/",
    #     include("apps.support.video.urls_internal"),
    # ),
]

# =========================
# DEV ONLY: media static
# =========================
if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )
