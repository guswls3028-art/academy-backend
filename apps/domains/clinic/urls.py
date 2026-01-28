# PATH: apps/domains/clinic/urls.py

from rest_framework.routers import DefaultRouter
from .views import (
    SessionViewSet,
    ParticipantViewSet,
    TestViewSet,
    SubmissionViewSet,
)

router = DefaultRouter()
router.register("sessions", SessionViewSet, basename="clinic-session")
router.register("participants", ParticipantViewSet, basename="clinic-participant")
router.register("tests", TestViewSet, basename="clinic-test")
router.register("submissions", SubmissionViewSet, basename="clinic-submission")

urlpatterns = router.urls
