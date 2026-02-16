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
    TenantListView,
    TenantDetailView,
    TenantCreateView,
    TenantOwnerView,
    TenantOwnerListView,
    TenantOwnerDetailView,
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
    path("tenants/", TenantListView.as_view(), name="core-tenants"),
    path("tenants/<int:tenant_id>/", TenantDetailView.as_view(), name="core-tenant-detail"),
    path("tenants/create/", TenantCreateView.as_view(), name="core-tenant-create"),
    path("tenants/<int:tenant_id>/owner/", TenantOwnerView.as_view(), name="core-tenant-owner"),
    path("tenants/<int:tenant_id>/owners/", TenantOwnerListView.as_view(), name="core-tenant-owners"),
    path("tenants/<int:tenant_id>/owners/<int:user_id>/", TenantOwnerDetailView.as_view(), name="core-tenant-owner-detail"),
    path("", include(router.urls)),
]
