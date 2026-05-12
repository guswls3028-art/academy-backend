# apps/api/config/urls.py

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from rest_framework_simplejwt.views import TokenRefreshView
from apps.api.common.views import health_check, healthz, readyz, sentry_test
from apps.api.common.auth_jwt import TenantAwareTokenObtainPairView

urlpatterns = [
    # =========================
    # Health Check
    # =========================
    path("health", health_check, name="health_check"),
    path("healthz", healthz, name="healthz"),
    path("readyz", readyz, name="readyz"),
    path("sentry-test/", sentry_test, name="sentry_test"),
    
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
    #     include("apps.domains.video.urls_internal"),
    # ),
]

# =========================
# Media static (profile photos, etc.)
# =========================
# 프로덕션에서도 /media/ 경로 서빙. S3 전환 시 제거 예정 (V1.1.0)
urlpatterns += static(
    settings.MEDIA_URL,
    document_root=settings.MEDIA_ROOT,
)

# =========================
# debug_toolbar (dev only) — 2026-05-13: djdt namespace 등록.
# 미등록 시 toolbar middleware 가 응답에 inject 하다 NoReverseMatch.
# settings.DEBUG=True && debug_toolbar installed 일 때만 활성화.
# =========================
if settings.DEBUG:
    try:
        import debug_toolbar  # noqa: F401
        urlpatterns += [path("__debug__/", include("debug_toolbar.urls"))]
    except ImportError:
        pass
