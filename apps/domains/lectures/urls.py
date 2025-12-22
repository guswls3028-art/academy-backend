from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    LectureViewSet,
    SessionViewSet,
)

router = DefaultRouter()

# =========================
# Core Lecture Domain
# =========================
router.register(r"lectures", LectureViewSet, basename="lectures")
router.register(r"sessions", SessionViewSet, basename="sessions")

urlpatterns = [
    path("", include(router.urls)),
]
