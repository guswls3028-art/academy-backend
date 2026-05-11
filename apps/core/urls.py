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
    LandingConsultPublicView,
    LandingConsultAdminListView,
    LandingConsultAdminDetailView,
    LandingTestimonialPublicView,
    LandingTestimonialPublicListView,
    LandingTestimonialAdminListView,
    LandingTestimonialAdminDetailView,
    LandingSitemapView,
    LandingManifestView,
    LandingHitReportToggleView,
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
    # 상담 요청 form (외부 학부모 → 학원장)
    path("landing/consult/", LandingConsultPublicView.as_view(), name="core-landing-consult-public"),
    path("landing/admin/consult/", LandingConsultAdminListView.as_view(), name="core-landing-consult-admin-list"),
    path("landing/admin/consult/<int:item_id>/", LandingConsultAdminDetailView.as_view(), name="core-landing-consult-admin-detail"),
    # 학부모 후기 — 공개 제출 + 어드민 승인
    path("landing/testimonial/", LandingTestimonialPublicView.as_view(), name="core-landing-testimonial-public"),
    path("landing/testimonial/public/", LandingTestimonialPublicListView.as_view(), name="core-landing-testimonial-public-list"),
    path("landing/admin/testimonial/", LandingTestimonialAdminListView.as_view(), name="core-landing-testimonial-admin-list"),
    path("landing/admin/testimonial/<int:item_id>/", LandingTestimonialAdminDetailView.as_view(), name="core-landing-testimonial-admin-detail"),
    # SEO sitemap.xml
    path("landing/sitemap.xml", LandingSitemapView.as_view(), name="core-landing-sitemap"),
    path("landing/manifest.json", LandingManifestView.as_view(), name="core-landing-manifest"),
    # 적중보고서 한 클릭 토글 (어드민 적중보고서 리스트에서 직접 토글)
    path("landing/admin/hit-report-toggle/", LandingHitReportToggleView.as_view(), name="core-landing-hit-report-toggle"),
    path("", include(router.urls)),
]
