from django.contrib import admin
from django.urls import path, include
from django.conf import settings
import sys

from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)

from django.conf.urls.static import static


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
if settings.DEBUG:
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
        document_root=settings.MEDIA_ROOT,
    )
