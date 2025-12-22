from django.contrib import admin
from django.urls import path, include
from django.conf import settings
import sys

# HLS개발환경용
from django.conf.urls.static import static
# 삭제예정

from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)

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
    # API v1
    # =========================
    path("api/v1/", include("apps.api.v1.urls")),
    ]

# =========================
# Debug Toolbar (DEBUG only)
# =========================
if settings.DEBUG and "runserver" in sys.argv:
    import debug_toolbar
    urlpatterns += [
        path("__debug__/", include(debug_toolbar.urls)),
    ]


# HLS 테스트용 개발환경용 삭제예정
urlpatterns += static(
    settings.MEDIA_URL,
    document_root=settings.MEDIA_ROOT,
)
