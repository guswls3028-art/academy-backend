from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from academy.adapters.db.django.repositories_ai import DjangoAIJobRepository
from apps.core.models import Tenant
from apps.core.management.commands.reconcile_stale_ai_jobs import (
    iter_stale_matchup_candidates,
    reconcile_candidates,
)
from apps.domains.ai.models import AIJobModel, AIResultModel
from apps.domains.inventory.models import InventoryFile
from apps.domains.matchup.models import MatchupDocument


class AIStaleJobReconciliationTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="ai-stale", name="AI stale")
        self.inv = InventoryFile.objects.create(
            tenant=self.tenant,
            scope="admin",
            display_name="source.pdf",
            original_name="source.pdf",
            r2_key="tenants/ai-stale/source.pdf",
            size_bytes=0,
            content_type="application/pdf",
        )
        self.inv2 = InventoryFile.objects.create(
            tenant=self.tenant,
            scope="admin",
            display_name="source2.pdf",
            original_name="source2.pdf",
            r2_key="tenants/ai-stale/source2.pdf",
            size_bytes=0,
            content_type="application/pdf",
        )

    def _job(self, job_id: str, source_id: str, *, status: str = "RUNNING") -> AIJobModel:
        old = timezone.now() - timedelta(days=3)
        job = AIJobModel.objects.create(
            tenant_id=str(self.tenant.id),
            job_id=job_id,
            job_type="matchup_analysis",
            status=status,
            source_domain="matchup",
            source_id=source_id,
            tier="basic",
            locked_by="sqs-worker",
            locked_at=old,
            lease_expires_at=old,
        )
        AIJobModel.objects.filter(pk=job.pk).update(created_at=old, updated_at=old)
        job.refresh_from_db()
        return job

    def test_reconciles_only_orphan_or_superseded_stale_running_jobs(self):
        done_doc = MatchupDocument.objects.create(
            tenant=self.tenant,
            inventory_file=self.inv,
            title="done",
            r2_key="tenants/ai-stale/source.pdf",
            original_name="source.pdf",
            status="done",
            ai_job_id="current-job",
        )
        processing_doc = MatchupDocument.objects.create(
            tenant=self.tenant,
            inventory_file=self.inv2,
            title="processing",
            r2_key="tenants/ai-stale/source2.pdf",
            original_name="source2.pdf",
            status="processing",
            ai_job_id="current-running-job",
        )
        superseded = self._job("old-job", str(done_doc.id))
        orphan = self._job("orphan-job", "999999")
        current = self._job("current-running-job", str(processing_doc.id))

        candidates = iter_stale_matchup_candidates(older_than_hours=24, limit=10)

        assert {c.job_id for c in candidates} == {superseded.job_id, orphan.job_id}

        assert reconcile_candidates(candidates, execute=False) == 0
        superseded.refresh_from_db()
        assert superseded.status == "RUNNING"

        assert reconcile_candidates(candidates, execute=True) == 2
        superseded.refresh_from_db()
        orphan.refresh_from_db()
        current.refresh_from_db()
        assert superseded.status == "FAILED"
        assert orphan.status == "FAILED"
        assert current.status == "RUNNING"
        assert superseded.locked_by is None
        assert superseded.lease_expires_at is None
        assert superseded.error_message.startswith("stale_running_reconciled:superseded_source")

    def test_reconciles_current_job_when_source_document_is_already_done(self):
        doc = MatchupDocument.objects.create(
            tenant=self.tenant,
            inventory_file=self.inv,
            title="done-current",
            r2_key="tenants/ai-stale/source.pdf",
            original_name="source.pdf",
            status="done",
            ai_job_id="current-done-job",
            problem_count=24,
        )
        job = self._job("current-done-job", str(doc.id))

        candidates = iter_stale_matchup_candidates(older_than_hours=24, limit=10)

        assert len(candidates) == 1
        assert candidates[0].job_id == job.job_id
        assert candidates[0].reason == "current_source_terminal:done"
        assert candidates[0].action == "mark_done_from_terminal_source"

        assert reconcile_candidates(candidates, execute=True) == 1
        job.refresh_from_db()
        assert job.status == "DONE"
        assert job.completed_at is not None
        assert job.error_message == ""
        result = AIResultModel.objects.get(job=job)
        assert result.payload["reconciled_from_source"] is True
        assert result.payload["source_id"] == str(doc.id)

    def test_mark_running_rejects_same_worker_duplicate_before_lease_expiry(self):
        now = timezone.now()
        job = AIJobModel.objects.create(
            tenant_id=str(self.tenant.id),
            job_id="same-worker-running",
            job_type="matchup_analysis",
            status="RUNNING",
            source_domain="matchup",
            source_id="1",
            tier="basic",
            locked_by="sqs-worker",
            locked_at=now,
            lease_expires_at=now + timedelta(minutes=10),
        )

        accepted = DjangoAIJobRepository().mark_running(
            job.job_id,
            "sqs-worker",
            now + timedelta(hours=1),
            now,
        )

        assert accepted is False

    def test_mark_running_allows_takeover_after_lease_expiry(self):
        now = timezone.now()
        job = AIJobModel.objects.create(
            tenant_id=str(self.tenant.id),
            job_id="expired-running",
            job_type="matchup_analysis",
            status="RUNNING",
            source_domain="matchup",
            source_id="1",
            tier="basic",
            locked_by="sqs-worker",
            locked_at=now - timedelta(hours=2),
            lease_expires_at=now - timedelta(minutes=1),
        )

        accepted = DjangoAIJobRepository().mark_running(
            job.job_id,
            "sqs-worker",
            now + timedelta(hours=1),
            now,
        )

        assert accepted is True
        job.refresh_from_db()
        assert job.lease_expires_at == now + timedelta(hours=1)
