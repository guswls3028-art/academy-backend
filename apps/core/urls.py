# PATH: apps/core/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.core.views import (
    MeView,
    ProgramView,
    ProfileViewSet,
    MyAttendanceViewSet,
    MyExpenseViewSet,
    JobProgressView,
    TenantBrandingView,
    TenantBrandingUploadLogoView,
)

router = DefaultRouter()
router.register("profile", ProfileViewSet, basename="profile")
router.register("profile/attendance", MyAttendanceViewSet, basename="my-attendance")
router.register("profile/expenses", MyExpenseViewSet, basename="my-expense")

urlpatterns = [
    path("me/", MeView.as_view(), name="core-me"),
    path("program/", ProgramView.as_view(), name="core-program"),
    path("job_progress/<str:job_id>/", JobProgressView.as_view(), name="core-job-progress"),
    path("tenant-branding/<int:tenant_id>/", TenantBrandingView.as_view(), name="core-tenant-branding"),
    path("tenant-branding/<int:tenant_id>/upload-logo/", TenantBrandingUploadLogoView.as_view(), name="core-tenant-branding-upload-logo"),
    path("", include(router.urls)),
]
