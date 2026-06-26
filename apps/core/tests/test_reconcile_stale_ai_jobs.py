from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.core.models import Tenant
from apps.core.management.commands.reconcile_stale_ai_jobs import (
    iter_stale_matchup_candidates,
    reconcile_candidates,
)
from apps.domains.ai.models import AIJobModel
from apps.domains.inventory.models import InventoryFile
from apps.domains.matchup.models import MatchupDocument


class ReconcileStaleAIJobsTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            code="ai-reconcile",
            name="AI Reconcile",
            is_active=True,
        )
        self.inventory_file = InventoryFile.objects.create(
            tenant=self.tenant,
            scope="admin",
            display_name="stale.pdf",
            r2_key=f"tests/{uuid.uuid4()}.pdf",
            original_name="stale.pdf",
            content_type="application/pdf",
        )

    def test_processing_source_recovery_is_opt_in(self):
        job_id = str(uuid.uuid4())
        expired_at = timezone.now() - timedelta(hours=3)
        doc = MatchupDocument.objects.create(
            tenant=self.tenant,
            inventory_file=self.inventory_file,
            title="Stale processing doc",
            r2_key=f"tests/{uuid.uuid4()}.pdf",
            original_name="stale.pdf",
            status="processing",
            ai_job_id=job_id,
        )
        AIJobModel.objects.create(
            job_id=job_id,
            job_type="matchup_analysis",
            status="RUNNING",
            tenant_id=str(self.tenant.id),
            source_domain="matchup",
            source_id=str(doc.id),
            locked_by="ai-sqs-worker",
            locked_at=expired_at,
            lease_expires_at=expired_at,
            last_heartbeat_at=expired_at,
            started_at=expired_at,
        )

        self.assertEqual(
            iter_stale_matchup_candidates(older_than_hours=1, limit=10),
            [],
        )

        candidates = iter_stale_matchup_candidates(
            older_than_hours=1,
            limit=10,
            include_processing_source=True,
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].action, "retry_processing_source")

    def test_processing_source_execute_marks_failed_then_retries(self):
        job_id = str(uuid.uuid4())
        expired_at = timezone.now() - timedelta(hours=3)
        doc = MatchupDocument.objects.create(
            tenant=self.tenant,
            inventory_file=self.inventory_file,
            title="Stale processing doc",
            r2_key=f"tests/{uuid.uuid4()}.pdf",
            original_name="stale.pdf",
            status="processing",
            ai_job_id=job_id,
        )
        job = AIJobModel.objects.create(
            job_id=job_id,
            job_type="matchup_analysis",
            status="RUNNING",
            tenant_id=str(self.tenant.id),
            source_domain="matchup",
            source_id=str(doc.id),
            locked_by="ai-sqs-worker",
            locked_at=expired_at,
            lease_expires_at=expired_at,
            last_heartbeat_at=expired_at,
            started_at=expired_at,
        )
        candidates = iter_stale_matchup_candidates(
            older_than_hours=1,
            limit=10,
            include_processing_source=True,
        )

        with patch("apps.domains.matchup.services.retry_document") as retry_document:
            with self.captureOnCommitCallbacks(execute=True):
                updated = reconcile_candidates(candidates, execute=True)

        self.assertEqual(updated, 1)
        job.refresh_from_db()
        doc.refresh_from_db()
        self.assertEqual(job.status, "FAILED")
        self.assertIn("expired_processing_source", job.error_message)
        self.assertEqual(doc.status, "failed")
        self.assertIn("expired_processing_source", doc.error_message)
        retry_document.assert_called_once()
        retried_doc = retry_document.call_args.args[0]
        self.assertEqual(retried_doc.id, doc.id)
        self.assertEqual(retry_document.call_args.kwargs, {"require_failed": True})
