from __future__ import annotations

from django.test import SimpleTestCase

from apps.domains.ai.gateway import dispatch_job


class DispatchJobContractTests(SimpleTestCase):
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
