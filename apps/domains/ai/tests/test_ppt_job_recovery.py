from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.ai.models import AIJobModel, AIResultModel
from apps.domains.ai.views.job_progress_view import JobProgressView
from apps.domains.ai.views.job_status_view import JobStatusView


User = get_user_model()


class PptJobRecoveryTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="PPT recovery", code="ppt_recovery", is_active=True)
        self.other_tenant = Tenant.objects.create(name="Other PPT", code="other_ppt", is_active=True)
        self.owner = self._staff("ppt_owner", self.tenant)
        self.peer = self._staff("ppt_peer", self.tenant)
        self.other = self._staff("ppt_other", self.other_tenant)
        self.key = f"tenants/{self.tenant.id}/tools/ppt/aaaaaaaaaaaa.pptx"
        self.job = AIJobModel.objects.create(
            job_id="ppt-owned-recovery",
            job_type="ppt_generation",
            status="DONE",
            tenant_id=str(self.tenant.id),
            source_domain="tools",
            payload={"owner_user_id": str(self.owner.id)},
        )
        AIResultModel.objects.create(job=self.job, payload={
            "r2_key": self.key,
            "download_url": "https://storage.invalid/expired",
            "filename": "presentation_aaaaaaaaaaaa.pptx",
            "slide_count": 2,
            "size_bytes": 42,
        })

    @staticmethod
    def _staff(username, tenant):
        user = User.objects.create_user(username=username, password="test1234", tenant=tenant, is_staff=True)
        TenantMembership.ensure_active(tenant=tenant, user=user, role="admin")
        return user

    def _get(self, view, suffix, *, user=None, tenant=None):
        request = self.factory.get(f"/api/v1/jobs/{self.job.job_id}/{suffix}")
        request.tenant = tenant or self.tenant
        force_authenticate(request, user=user or self.owner)
        return view.as_view()(request, job_id=self.job.job_id)

    @patch("apps.infrastructure.storage.r2.generate_presigned_get_url_storage")
    @patch("apps.domains.ai.views.job_progress_view.get_job_status_from_redis")
    def test_completed_job_gets_new_download_url_after_original_expiry(self, redis_status, presign):
        redis_status.return_value = {
            "job_id": self.job.job_id,
            "job_type": "ppt_generation",
            "status": "DONE",
            "result": {"download_url": "https://storage.invalid/expired"},
        }
        presign.side_effect = ["https://storage.invalid/fresh-1", "https://storage.invalid/fresh-2"]

        status_response = self._get(JobStatusView, "")
        progress_response = self._get(JobProgressView, "progress/")

        self.assertEqual(status_response.status_code, 200, status_response.data)
        self.assertEqual(progress_response.status_code, 200, progress_response.data)
        self.assertEqual(status_response.data["result"]["download_url"], "https://storage.invalid/fresh-1")
        self.assertEqual(progress_response.data["result"]["download_url"], "https://storage.invalid/fresh-2")
        self.assertNotIn("r2_key", status_response.data["result"])
        self.assertNotIn("r2_key", progress_response.data["result"])
        self.assertEqual(presign.call_count, 2)
        self.assertEqual(presign.call_args.kwargs["key"], self.key)
        self.assertEqual(presign.call_args.kwargs["expires_in"], 3600)

    @patch("apps.infrastructure.storage.r2.generate_presigned_get_url_storage")
    @patch("apps.domains.ai.views.job_progress_view.get_job_status_from_redis", return_value=None)
    def test_other_user_or_tenant_cannot_recover_job(self, _redis_status, presign):
        for user, tenant in ((self.peer, self.tenant), (self.other, self.other_tenant)):
            for view, suffix in ((JobStatusView, ""), (JobProgressView, "progress/")):
                with self.subTest(user=user.username, view=view.__name__):
                    response = self._get(view, suffix, user=user, tenant=tenant)
                    if tenant == self.other_tenant and view == JobProgressView:
                        self.assertEqual(response.status_code, 200, response.data)
                        self.assertEqual(response.data["status"], "UNKNOWN")
                    else:
                        self.assertEqual(response.status_code, 404, response.data)
                    self.assertNotIn("result", response.data)
        presign.assert_not_called()

    @patch("apps.domains.ai.views.job_progress_view.get_job_status_from_redis")
    def test_cached_progress_also_denies_other_staff_member(self, redis_status):
        redis_status.return_value = {
            "job_id": self.job.job_id,
            "job_type": "ppt_generation",
            "status": "DONE",
            "result": {"download_url": "https://storage.invalid/expired"},
        }

        response = self._get(JobProgressView, "progress/", user=self.peer)

        self.assertEqual(response.status_code, 404, response.data)
        self.assertNotIn("result", response.data)

    @patch("apps.infrastructure.storage.r2.generate_presigned_get_url_storage")
    def test_unexpected_result_key_cannot_be_resigned_or_exposed(self, presign):
        result = AIResultModel.objects.get(job=self.job)
        result.payload["r2_key"] = f"tenants/{self.other_tenant.id}/tools/ppt/aaaaaaaaaaaa.pptx"
        result.save(update_fields=["payload"])

        response = self._get(JobStatusView, "")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn("download_url", response.data["result"])
        self.assertNotIn("r2_key", response.data["result"])
        presign.assert_not_called()
