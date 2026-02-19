# PATH: apps/domains/clinic/urls.py

from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import (
    SessionViewSet,
    ParticipantViewSet,
    TestViewSet,
    SubmissionViewSet,
)
from .idcard_views import StudentClinicIdcardView

router = DefaultRouter()
router.register("sessions", SessionViewSet, basename="clinic-session")
router.register("participants", ParticipantViewSet, basename="clinic-participant")
router.register("tests", TestViewSet, basename="clinic-test")
router.register("submissions", SubmissionViewSet, basename="clinic-submission")

urlpatterns = [
    path("idcard/", StudentClinicIdcardView.as_view(), name="clinic-idcard"),
] + router.urls
