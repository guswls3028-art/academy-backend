from __future__ import annotations

import uuid
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from academy.adapters.db.django.uow import DjangoUnitOfWork
from academy.application.use_cases.ai.process_ai_job_from_sqs import prepare_ai_job
from apps.domains.ai.models import AIJobModel


class AIWorkerTenantGuardTests(TestCase):
    def _job(self, tenant_id: str = "1") -> AIJobModel:
        return AIJobModel.objects.create(
            job_id=str(uuid.uuid4()),
            job_type="ocr",
            status="PENDING",
            tenant_id=tenant_id,
            payload={"tenant_id": tenant_id},
            tier="basic",
            source_domain="submissions",
            source_id="1",
        )

    def test_prepare_ai_job_rejects_missing_message_tenant_id(self):
        now = timezone.now()
        job = self._job("1")

        prepared = prepare_ai_job(
            DjangoUnitOfWork(),
            job_id=job.job_id,
            receipt_handle="receipt",
            tier="basic",
            payload={"tenant_id": "1"},
            job_type="ocr",
            tenant_id=None,
            source_domain="submissions",
            source_id="1",
            now=now,
        )

        assert prepared is None
        job.refresh_from_db()
        assert job.status != "RUNNING"
        assert job.locked_by is None
        assert job.started_at is None
        assert job.completed_at is not None
        assert job.error_message == "missing_tenant_id_in_sqs_message"

    def test_prepare_ai_job_rejects_message_db_tenant_mismatch(self):
        now = timezone.now()
        job = self._job("1")

        prepared = prepare_ai_job(
            DjangoUnitOfWork(),
            job_id=job.job_id,
            receipt_handle="receipt",
            tier="basic",
            payload={"tenant_id": "2"},
            job_type="ocr",
            tenant_id="2",
            source_domain="submissions",
            source_id="1",
            now=now,
        )

        assert prepared is None
        job.refresh_from_db()
        assert job.status != "RUNNING"
        assert job.locked_by is None
        assert job.started_at is None
        assert job.completed_at is not None
        assert job.error_message == "tenant_mismatch_in_sqs_message"

    def test_prepare_ai_job_rejects_payload_tenant_mismatch(self):
        now = timezone.now()
        job = self._job("1")

        prepared = prepare_ai_job(
            DjangoUnitOfWork(),
            job_id=job.job_id,
            receipt_handle="receipt",
            tier="basic",
            payload={"tenant_id": "2"},
            job_type="ocr",
            tenant_id="1",
            source_domain="submissions",
            source_id="1",
            now=now,
        )

        assert prepared is None
        job.refresh_from_db()
        assert job.status != "RUNNING"
        assert job.locked_by is None
        assert job.started_at is None
        assert job.completed_at is not None
        assert job.error_message == "payload_tenant_mismatch_in_sqs_message"

    def test_prepare_ai_job_does_not_overwrite_terminal_job_for_bad_message(self):
        now = timezone.now()
        job = self._job("1")
        job.status = "DONE"
        job.completed_at = now - timedelta(minutes=1)
        job.error_message = ""
        job.save(update_fields=["status", "completed_at", "error_message", "updated_at"])

        prepared = prepare_ai_job(
            DjangoUnitOfWork(),
            job_id=job.job_id,
            receipt_handle="receipt",
            tier="basic",
            payload={"tenant_id": "2"},
            job_type="ocr",
            tenant_id="2",
            source_domain="submissions",
            source_id="1",
            now=now,
        )

        assert prepared is None
        job.refresh_from_db()
        assert job.status == "DONE"
        assert job.locked_by is None
        assert job.error_message == ""

    def test_prepare_ai_job_allows_matching_tenant_id(self):
        now = timezone.now()
        job = self._job("1")

        prepared = prepare_ai_job(
            DjangoUnitOfWork(),
            job_id=job.job_id,
            receipt_handle="receipt",
            tier="basic",
            payload={"tenant_id": "1"},
            job_type="ocr",
            tenant_id="1",
            source_domain="submissions",
            source_id="1",
            worker_id="test-worker",
            lease_seconds=60,
            now=now,
        )

        assert prepared is not None
        assert prepared.job_id == job.job_id
        assert prepared.tenant_id == "1"
        job.refresh_from_db()
        assert job.status == "RUNNING"
        assert job.locked_by == "test-worker"
        assert job.started_at == now
        assert job.lease_expires_at == now + timedelta(seconds=60)
