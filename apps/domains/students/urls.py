# PATH: apps/domains/students/urls.py

from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import (
    StudentViewSet,
    TagViewSet,
    RegistrationRequestViewSet,
    StudentPasswordFindRequestView,
    StudentPasswordFindVerifyView,
    StudentPasswordResetSendView,
)

router = DefaultRouter()

# 🔥 basename 명시 (queryset 없는 ViewSet 대응)
router.register(r"tags", TagViewSet, basename="student-tag")
router.register(r"registration_requests", RegistrationRequestViewSet, basename="student-registration-request")
router.register(r"", StudentViewSet, basename="student")

urlpatterns = [
    path("password_find/request/", StudentPasswordFindRequestView.as_view(), name="student-password-find-request"),
    path("password_find/verify/", StudentPasswordFindVerifyView.as_view(), name="student-password-find-verify"),
    path("password_reset_send/", StudentPasswordResetSendView.as_view(), name="student-password-reset-send"),
] + router.urls
