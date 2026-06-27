from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.models import Tenant
from apps.domains.ai.models import AIJobModel
from apps.domains.inventory.models import InventoryFile, InventoryFolder
from apps.domains.matchup.models import MatchupDocument
from apps.domains.matchup.views import _reconcile_document_from_ai_job

pytestmark = pytest.mark.django_db


def _document(*, tenant: Tenant, job_id: str) -> MatchupDocument:
    folder = InventoryFolder.objects.create(
        tenant=tenant,
        scope="admin",
        student_ps="",
        parent=None,
        name="root",
    )
    inv = InventoryFile.objects.create(
        tenant=tenant,
        folder=folder,
        scope="admin",
        student_ps="",
        original_name="stale.pdf",
        r2_key=f"tenants/{tenant.id}/stale.pdf",
        size_bytes=100,
        content_type="application/pdf",
    )
    return MatchupDocument.objects.create(
        tenant=tenant,
        inventory_file=inv,
        title="stale",
        r2_key=inv.r2_key,
        original_name=inv.original_name,
        size_bytes=inv.size_bytes,
        content_type=inv.content_type,
        status="processing",
        ai_job_id=job_id,
    )


def test_reconcile_marks_expired_running_matchup_job_failed():
    tenant = Tenant.objects.create(name="stale-ai", code="stale-ai")
    job = AIJobModel.objects.create(
        job_id="expired-running-job",
        job_type="matchup_analysis",
        status="RUNNING",
        tenant_id=str(tenant.id),
        tier="basic",
        source_domain="matchup",
        source_id="0",
        lease_expires_at=timezone.now() - timedelta(minutes=11),
    )
    doc = _document(tenant=tenant, job_id=job.job_id)
    job.source_id = str(doc.id)
    job.save(update_fields=["source_id", "updated_at"])

    assert _reconcile_document_from_ai_job(doc) is True

    doc.refresh_from_db()
    job.refresh_from_db()
    assert doc.status == "failed"
    assert doc.error_message == "AI 작업이 중단되었습니다. 다시 시도해 주세요."
    assert job.status == "FAILED"
    assert job.lease_expires_at is None
