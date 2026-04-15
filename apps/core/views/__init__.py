# PATH: apps/core/views/__init__.py
# Re-export all public symbols for backward compatibility.
# Existing imports like `from apps.core.views import MeView` continue to work.

from apps.core.views.auth import MeView, ChangePasswordView
from apps.core.views.program import ProgramView, SubscriptionView
from apps.core.views.profile import ProfileViewSet
from apps.core.views.attendance import MyAttendanceViewSet
from apps.core.views.expense import MyExpenseViewSet
from apps.core.views.job_progress import JobProgressView
from apps.core.views.tenant_branding import (
    TenantBrandingView,
    TenantBrandingUploadLogoView,
)
from apps.core.views.tenant_management import (
    TenantListView,
    TenantDetailView,
    TenantCreateView,
    TenantOwnerView,
    TenantOwnerListView,
    TenantOwnerDetailView,
)
from apps.core.views.tenant_info import (
    MaintenanceModeView,
    TenantInfoView,
    PublicOgMetaView,
    LegalConfigView,
)

__all__ = [
    "MeView",
    "ChangePasswordView",
    "ProgramView",
    "SubscriptionView",
    "ProfileViewSet",
    "MyAttendanceViewSet",
    "MyExpenseViewSet",
    "JobProgressView",
    "TenantBrandingView",
    "TenantBrandingUploadLogoView",
    "TenantListView",
    "TenantDetailView",
    "TenantCreateView",
    "TenantOwnerView",
    "TenantOwnerListView",
    "TenantOwnerDetailView",
    "MaintenanceModeView",
    "TenantInfoView",
    "PublicOgMetaView",
    "LegalConfigView",
]
