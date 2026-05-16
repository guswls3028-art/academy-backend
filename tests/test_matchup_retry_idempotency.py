from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import TestCase

from apps.core.models import Tenant
from apps.domains.inventory.models import InventoryFile
from apps.domains.matchup.models import MatchupDocument, MatchupProblem
from apps.domains.matchup.services import retry_document


class MatchupRetryIdempotencyTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="t-retry-idem", name="t-retry")
        self.inv = InventoryFile.objects.create(
            tenant=self.tenant,
            scope="admin",
            display_name="retry_source.pdf",
            original_name="retry_source.pdf",
            r2_key="tenants/x/matchup/retry/source.pdf",
            size_bytes=0,
            content_type="application/pdf",
        )
        self.doc = MatchupDocument.objects.create(
            tenant=self.tenant,
            inventory_file=self.inv,
            title="retry_doc",
            r2_key="tenants/x/matchup/retry/source.pdf",
            original_name="source.pdf",
            status="failed",
            problem_count=2,
            meta={"source_type": "academy_workbook"},
        )

    def _create_problem(self, number: int, meta: dict) -> MatchupProblem:
        return MatchupProblem.objects.create(
            tenant=self.tenant,
            document=self.doc,
            number=number,
            text=f"problem {number}",
            image_key="",
            meta=meta,
        )

    @patch("apps.domains.ai.gateway.dispatch_job")
    @patch("apps.infrastructure.storage.r2.generate_presigned_get_url_storage")
    def test_retry_locks_document_and_moves_to_processing(self, presign, dispatch):
        presign.return_value = "https://example.test/source.pdf"
        dispatch.return_value = {"ok": True, "job_id": "job-retry-1", "type": "matchup_analysis"}
        self._create_problem(1, {"page_index": 0})
        manual = self._create_problem(2, {"manual": True, "page_index": 0})

        job_id = retry_document(self.doc, require_failed=True)

        self.doc.refresh_from_db()
        assert job_id == "job-retry-1"
        assert self.doc.status == "processing"
        assert self.doc.ai_job_id == "job-retry-1"
        assert self.doc.problem_count == 0
        assert list(self.doc.problems.values_list("id", flat=True)) == [manual.id]
        dispatch.assert_called_once()
        assert dispatch.call_args.kwargs["source_domain"] == "matchup"
        assert dispatch.call_args.kwargs["source_id"] == str(self.doc.id)

    @patch("apps.domains.ai.gateway.dispatch_job")
    @patch("apps.infrastructure.storage.r2.generate_presigned_get_url_storage")
    def test_retry_rejects_processing_document_without_dispatch(self, presign, dispatch):
        self.doc.status = "processing"
        self.doc.ai_job_id = "existing-job"
        self.doc.save(update_fields=["status", "ai_job_id", "updated_at"])

        with pytest.raises(RuntimeError, match="이미 처리 중"):
            retry_document(self.doc, require_failed=True)

        presign.assert_not_called()
        dispatch.assert_not_called()

    @patch("apps.domains.ai.gateway.dispatch_job")
    @patch("apps.infrastructure.storage.r2.generate_presigned_get_url_storage")
    def test_retry_requires_failed_status_when_called_from_retry_api(self, presign, dispatch):
        self.doc.status = "done"
        self.doc.save(update_fields=["status", "updated_at"])

        with pytest.raises(RuntimeError, match="재시도는 실패 상태"):
            retry_document(self.doc, require_failed=True)

        presign.assert_not_called()
        dispatch.assert_not_called()
