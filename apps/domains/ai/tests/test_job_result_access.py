from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.core.views.job_progress import JobProgressView as CoreJobProgressView
from apps.domains.ai.models import AIJobModel, AIResultModel
from apps.domains.ai.views.job_progress_view import JobProgressView
from apps.domains.ai.views.job_status_view import JobStatusView
from apps.domains.students.test_support import create_student_fixture


User = get_user_model()


class JobResultAccessTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Job Result Access",
            code="job-result-access",
            is_active=True,
        )
        self.staff = User.objects.create_user(
            username="job_result_staff",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.staff,
            role="admin",
        )
        self.student = User.objects.create_user(
            username="job_result_student",
            password="test1234",
            tenant=self.tenant,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.student,
            role="student",
        )

        create_student_fixture(
            tenant=self.tenant,
            user=self.student,
            ps_number="job-result-student",
            omr_code="12345678",
            name="작업 결과 학생",
            phone="01012345678",
            parent_phone="01087654321",
        )

    def _request(self, path: str, *, user):
        request = self.factory.get(path)
        request.tenant = self.tenant
        force_authenticate(request, user=user)
        return request

    def _job(self, *, job_id: str, job_type: str):
        job = AIJobModel.objects.create(
            job_id=job_id,
            job_type=job_type,
            status="DONE",
            tenant_id=str(self.tenant.id),
            source_domain="staff",
            source_id="1",
            payload={},
        )
        AIResultModel.objects.create(
            job=job,
            payload={"download_url": "https://storage.invalid/private.xlsx"},
        )
        return job

    @patch(
        "apps.domains.ai.views.job_progress_view.get_job_status_from_redis",
        return_value=None,
    )
    def test_student_cannot_read_staff_or_unknown_job_results(
        self,
        _mock_redis_status,
    ):
        jobs = [
            self._job(job_id="staff-export", job_type="staff_excel_export"),
            self._job(job_id="future-sensitive", job_type="future_sensitive_job"),
        ]

        for job in jobs:
            with self.subTest(job_type=job.job_type, endpoint="status"):
                response = JobStatusView.as_view()(
                    self._request(
                        f"/api/v1/jobs/{job.job_id}/",
                        user=self.student,
                    ),
                    job_id=job.job_id,
                )
                self.assertEqual(response.status_code, 404, response.data)
                self.assertNotIn("result", response.data)

            with self.subTest(job_type=job.job_type, endpoint="progress"):
                response = JobProgressView.as_view()(
                    self._request(
                        f"/api/v1/jobs/{job.job_id}/progress/",
                        user=self.student,
                    ),
                    job_id=job.job_id,
                )
                self.assertEqual(response.status_code, 404, response.data)
                self.assertNotIn("result", response.data)

            with self.subTest(job_type=job.job_type, endpoint="legacy-progress"):
                response = CoreJobProgressView.as_view()(
                    self._request(
                        f"/api/v1/core/job_progress/{job.job_id}/",
                        user=self.student,
                    ),
                    job_id=job.job_id,
                )
                self.assertEqual(response.status_code, 404, response.data)

    @patch(
        "apps.domains.ai.views.job_progress_view.get_job_status_from_redis",
        return_value=None,
    )
    def test_staff_can_read_current_job_results(self, _mock_redis_status):
        job = self._job(job_id="staff-export-authorized", job_type="staff_excel_export")

        status_response = JobStatusView.as_view()(
            self._request(
                f"/api/v1/jobs/{job.job_id}/",
                user=self.staff,
            ),
            job_id=job.job_id,
        )
        progress_response = JobProgressView.as_view()(
            self._request(
                f"/api/v1/jobs/{job.job_id}/progress/",
                user=self.staff,
            ),
            job_id=job.job_id,
        )
        legacy_response = CoreJobProgressView.as_view()(
            self._request(
                f"/api/v1/core/job_progress/{job.job_id}/",
                user=self.staff,
            ),
            job_id=job.job_id,
        )

        self.assertEqual(status_response.status_code, 200, status_response.data)
        self.assertEqual(progress_response.status_code, 200, progress_response.data)
        self.assertEqual(legacy_response.status_code, 200, legacy_response.data)
