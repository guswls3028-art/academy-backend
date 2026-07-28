from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.domains.ai.gateway import _publish_after_commit, dispatch_job
from apps.shared.contracts.ai_job import AIJob


class DispatchJobContractTests(SimpleTestCase):
    @patch("apps.domains.ai.gateway._propagate_publish_failure")
    @patch("apps.domains.ai.gateway.ai_repo.job_save_failed")
    @patch("apps.domains.ai.gateway.publish_job", return_value=False)
    def test_publisher_false_marks_job_failed(
        self,
        _publish,
        save_failed,
        propagate_failure,
    ):
        job = AIJob.new(
            type="wrong_note_pdf_generation",
            payload={"wrong_note_pdf_job_id": 7},
            tenant_id="1",
            source_domain="results_wrong_note_pdf",
            source_id="7",
        )
        job_model = SimpleNamespace(job_id=job.id)

        _publish_after_commit(job, job_model)

        save_failed.assert_called_once_with(
            job_model,
            "SQS publisher rejected the job",
            "SQS publisher rejected the job",
        )
        propagate_failure.assert_called_once_with(
            job_model,
            "SQS publisher rejected the job",
        )

    def test_rejects_missing_tenant_id_before_job_creation(self):
        result = dispatch_job(
            job_type="excel_parsing",
            payload={"tenant_id": "1"},
            tenant_id=None,
            source_domain="students",
            source_id="1",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["rejection_code"], "missing_tenant_id")
        self.assertIsNone(result["job_id"])

    def test_rejects_missing_source_domain_before_job_creation(self):
        result = dispatch_job(
            job_type="excel_parsing",
            payload={"tenant_id": "1"},
            tenant_id="1",
            source_domain=None,
            source_id="1",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["rejection_code"], "missing_source_domain")

    def test_rejects_payload_tenant_mismatch_before_job_creation(self):
        result = dispatch_job(
            job_type="excel_parsing",
            payload={"tenant_id": "2"},
            tenant_id="1",
            source_domain="students",
            source_id="1",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["rejection_code"], "tenant_mismatch")
