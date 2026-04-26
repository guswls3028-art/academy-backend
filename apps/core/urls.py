# PATH: apps/core/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.core.views import (
    MeView,
    ChangePasswordView,
    ProgramView,
    SubscriptionView,
    ProfileViewSet,
    MyAttendanceViewSet,
    MyExpenseViewSet,
    JobProgressView,
    TenantBrandingView,
    TenantBrandingUploadLogoView,
    MaintenanceModeView,
    TenantListView,
    TenantDetailView,
    TenantInfoView,
    TenantCreateView,
    TenantOwnerView,
    TenantOwnerListView,
    TenantOwnerDetailView,
    PublicOgMetaView,
    LegalConfigView,
    DevDashboardSummaryView,
    DevTenantUsageView,
    DevTenantActivityView,
    DevImpersonateView,
    DevTenantStorageView,
    DevGlobalSearchView,
    DevAuditLogListView,
    DevCronListView,
    DevCronTriggerView,
)
from apps.core.views_landing import (
    LandingPublicView,
    LandingHasPublishedView,
    LandingAdminView,
    LandingPublishView,
    LandingUnpublishView,
    LandingUploadImageView,
    LandingTemplatesView,
)

router = DefaultRouter()
router.register("profile", ProfileViewSet, basename="profile")
router.register("profile/attendance", MyAttendanceViewSet, basename="my-attendance")
router.register("profile/expenses", MyExpenseViewSet, basename="my-expense")

urlpatterns = [
    path("me/", MeView.as_view(), name="core-me"),
    path("change-password/", ChangePasswordView.as_view(), name="core-change-password"),
    path("program/", ProgramView.as_view(), name="core-program"),
    path("subscription/", SubscriptionView.as_view(), name="core-subscription"),
    path("job_progress/<str:job_id>/", JobProgressView.as_view(), name="core-job-progress"),
    path("tenant-branding/<int:tenant_id>/", TenantBrandingView.as_view(), name="core-tenant-branding"),
    path("tenant-branding/<int:tenant_id>/upload-logo/", TenantBrandingUploadLogoView.as_view(), name="core-tenant-branding-upload-logo"),
    path("maintenance-mode/", MaintenanceModeView.as_view(), name="core-maintenance-mode"),
    path("tenants/", TenantListView.as_view(), name="core-tenants"),
    path("tenants/<int:tenant_id>/", TenantDetailView.as_view(), name="core-tenant-detail"),
    path("tenant-info/", TenantInfoView.as_view(), name="core-tenant-info"),
    path("og-meta/", PublicOgMetaView.as_view(), name="core-og-meta"),
    path("legal-config/", LegalConfigView.as_view(), name="core-legal-config"),
    path("tenants/create/", TenantCreateView.as_view(), name="core-tenant-create"),
    path("tenants/<int:tenant_id>/owner/", TenantOwnerView.as_view(), name="core-tenant-owner"),
    path("tenants/<int:tenant_id>/owners/", TenantOwnerListView.as_view(), name="core-tenant-owners"),
    path("tenants/<int:tenant_id>/owners/<int:user_id>/", TenantOwnerDetailView.as_view(), name="core-tenant-owner-detail"),
    # Dev/운영 콘솔
    path("dev/dashboard/", DevDashboardSummaryView.as_view(), name="core-dev-dashboard"),
    path("dev/tenants/<int:tenant_id>/usage/", DevTenantUsageView.as_view(), name="core-dev-tenant-usage"),
    path("dev/tenants/<int:tenant_id>/activity/", DevTenantActivityView.as_view(), name="core-dev-tenant-activity"),
    path("dev/tenants/<int:tenant_id>/impersonate/", DevImpersonateView.as_view(), name="core-dev-tenant-impersonate"),
    path("dev/tenants/<int:tenant_id>/storage/", DevTenantStorageView.as_view(), name="core-dev-tenant-storage"),
    path("dev/search/", DevGlobalSearchView.as_view(), name="core-dev-search"),
    path("dev/audit/", DevAuditLogListView.as_view(), name="core-dev-audit"),
    path("dev/cron/", DevCronListView.as_view(), name="core-dev-cron-list"),
    path("dev/cron/run/", DevCronTriggerView.as_view(), name="core-dev-cron-run"),
    # Landing page
    path("landing/public/", LandingPublicView.as_view(), name="core-landing-public"),
    path("landing/has-published/", LandingHasPublishedView.as_view(), name="core-landing-has-published"),
    path("landing/admin/", LandingAdminView.as_view(), name="core-landing-admin"),
    path("landing/publish/", LandingPublishView.as_view(), name="core-landing-publish"),
    path("landing/unpublish/", LandingUnpublishView.as_view(), name="core-landing-unpublish"),
    path("landing/upload-image/", LandingUploadImageView.as_view(), name="core-landing-upload-image"),
    path("landing/templates/", LandingTemplatesView.as_view(), name="core-landing-templates"),
    path("", include(router.urls)),
]
