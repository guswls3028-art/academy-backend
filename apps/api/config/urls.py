from django.contrib import admin
from django.urls import path, include
from django.conf import settings
import sys

from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)

from django.conf.urls.static import static
from apps.support.media.views import HLSMediaServeView


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
# 🔥 HLS (API 바깥, 루트)
# =========================
urlpatterns += [
    path(
        "hls/videos/<int:video_id>/<path:path>",
        HLSMediaServeView.as_view(),
        name="hls-media-serve",
    ),
]

# =========================
# Debug Toolbar (DEBUG only)
# =========================
if settings.DEBUG and "runserver" in sys.argv:
    import debug_toolbar
    urlpatterns += [
        path("__debug__/", include(debug_toolbar.urls)),
    ]

# =========================
# DEV ONLY: media static
# =========================
if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDI_
