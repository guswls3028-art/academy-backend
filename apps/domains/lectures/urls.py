from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    LectureViewSet,
    SessionViewSet,
    SectionViewSet,
    SectionAssignmentViewSet,
)

router = DefaultRouter()

# =========================
# Core Lecture Domain
# =========================
router.register(r"lectures", LectureViewSet, basename="lectures")
router.register(r"sessions", SessionViewSet, basename="sessions")
router.register(r"sections", SectionViewSet, basename="sections")
router.register(r"section-assignments", SectionAssignmentViewSet, basename="section-assignments")

urlpatterns = [
    path("", include(router.urls)),
]
