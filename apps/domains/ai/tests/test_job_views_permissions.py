from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import TenantResolvedAndMember
from apps.core.views.job_progress import JobProgressView as CoreJobProgressView
from apps.domains.ai.views.job_progress_view import JobProgressView
from apps.domains.ai.views.job_status_view import JobStatusView


def test_ai_job_views_require_tenant_membership():
    for view_cls in (CoreJobProgressView, JobProgressView, JobStatusView):
        assert IsAuthenticated in view_cls.permission_classes
        assert TenantResolvedAndMember in view_cls.permission_classes
