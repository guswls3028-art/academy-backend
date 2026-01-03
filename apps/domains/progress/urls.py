# apps/domains/progress/urls.py
from rest_framework.routers import DefaultRouter

from .views import (
    ProgressPolicyViewSet,
    SessionProgressViewSet,
    LectureProgressViewSet,
    ClinicLinkViewSet,
    RiskLogViewSet,
)

router = DefaultRouter()
router.register("policies", ProgressPolicyViewSet, basename="progress-policy")
router.register("session-progress", SessionProgressViewSet, basename="session-progress")
router.register("lecture-progress", LectureProgressViewSet, basename="lecture-progress")
router.register("clinic-links", ClinicLinkViewSet, basename="clinic-link")
router.register("risk-logs", RiskLogViewSet, basename="risk-log")

urlpatterns = router.urls
