from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.ai.models import AIJobModel
from apps.domains.ai.views.job_progress_view import JobProgressView


User = get_user_model()


class JobProgressViewTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="AI Progress Tenant",
            code="ai_progress",
            is_active=True,
        )
        self.user = User.objects.create_user(
            username="ai_progress_owner",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="owner")

    def _request(self, job_id: str):
        request = self.factory.get(f"/api/v1/jobs/{job_id}/progress/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        return JobProgressView.as_view()(request, job_id=job_id)

    @patch("academy.adapters.cache.redis_progress_adapter.RedisProgressAdapter.get_progress", return_value=None)
    @patch("apps.domains.ai.views.job_progress_view.get_job_status_from_redis", return_value=None)
    def test_redis_miss_returns_pending_status_from_db(self, mock_redis_status, mock_progress):
        job = AIJobModel.objects.create(
            job_id="ppt-pending-job",
            job_type="ppt_generation",
            status="PENDING",
            payload={"mode": "images"},
            tenant_id=str(self.tenant.id),
            source_domain="tools",
            tier="basic",
        )

        response = self._request(job.job_id)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["job_id"], job.job_id)
        self.assertEqual(response.data["job_type"], "ppt_generation")
        self.assertEqual(response.data["status"], "PENDING")
        self.assertIsNone(response.data["progress"])
        self.assertIsNone(response.data["result"])
        mock_redis_status.assert_called_once_with(str(self.tenant.id), job.job_id)
        mock_progress.assert_called_once_with(job.job_id, tenant_id=str(self.tenant.id))

    @patch("apps.domains.ai.views.job_progress_view.get_job_status_from_redis", return_value=None)
    def test_redis_miss_does_not_leak_other_tenant_job_status(self, mock_redis_status):
        other_tenant = Tenant.objects.create(
            name="Other AI Progress Tenant",
            code="ai_progress_other",
            is_active=True,
        )
        job = AIJobModel.objects.create(
            job_id="other-tenant-job",
            job_type="ppt_generation",
            status="PENDING",
            payload={"mode": "images"},
            tenant_id=str(other_tenant.id),
            source_domain="tools",
            tier="basic",
        )

        response = self._request(job.job_id)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "UNKNOWN")
        self.assertNotIn("job_type", response.data)
        mock_redis_status.assert_called_once_with(str(self.tenant.id), job.job_id)
