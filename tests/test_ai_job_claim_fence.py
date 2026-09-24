"""An expired worker cannot close a job after a newer lease claim."""

from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone

from academy.adapters.db.django.uow import DjangoUnitOfWork
from academy.application.use_cases.ai.process_ai_job_from_sqs import (
    complete_ai_job,
    fail_ai_job,
    prepare_ai_job,
)
from apps.domains.ai.models import AIJobModel, AIResultModel


class AIJobClaimFenceTests(TestCase):
    def _claims(self):
        job = AIJobModel.objects.create(
            job_id=str(uuid4()),
            job_type="matchup_analysis",
            tenant_id="1",
            source_domain="matchup",
            source_id="1",
            tier="premium",
        )
        started_at = timezone.now()
        claims = []
        for receipt, now in (
            ("old", started_at),
            ("new", started_at + timedelta(seconds=61)),
        ):
            claim = prepare_ai_job(
                DjangoUnitOfWork(),
                job_id=job.job_id,
                receipt_handle=receipt,
                tier="premium",
                payload={"tenant_id": "1"},
                job_type=job.job_type,
                tenant_id="1",
                source_domain="matchup",
                source_id="1",
                worker_id="ai-sqs-worker",
                lease_seconds=60,
                now=now,
            )
            self.assertIsNotNone(claim)
            claims.append(claim)
        self.assertNotEqual(claims[0].claim_locked_at, claims[1].claim_locked_at)
        return job, claims[0], claims[1]

    @patch("apps.domains.ai.redis_status_cache.cache_job_status", return_value=True)
    def test_expired_claim_cannot_complete_or_fail_new_running_attempt(self, _cache):
        job, old, new = self._claims()

        self.assertFalse(complete_ai_job(
            DjangoUnitOfWork(), job.job_id, {"winner": "old"},
            expected_locked_at=old.claim_locked_at,
        ))
        self.assertFalse(fail_ai_job(
            DjangoUnitOfWork(), job.job_id, "old_error", tier="premium",
            expected_locked_at=old.claim_locked_at,
        ))
        job.refresh_from_db()
        self.assertEqual(job.status, "RUNNING")
        self.assertFalse(AIResultModel.objects.filter(job=job).exists())

        self.assertTrue(complete_ai_job(
            DjangoUnitOfWork(), job.job_id, {"winner": "new"},
            expected_locked_at=new.claim_locked_at,
        ))
        job.refresh_from_db()
        self.assertEqual(job.status, "DONE")
        self.assertEqual(AIResultModel.objects.get(job=job).payload, {"winner": "new"})

    @patch("apps.domains.ai.redis_status_cache.cache_job_status", return_value=True)
    def test_new_claim_can_fail_and_old_claim_cannot_repair_its_result(self, _cache):
        job, old, new = self._claims()

        self.assertTrue(fail_ai_job(
            DjangoUnitOfWork(), job.job_id, "new_error", tier="premium",
            expected_locked_at=new.claim_locked_at,
        ))
        self.assertFalse(fail_ai_job(
            DjangoUnitOfWork(), job.job_id, "old_error", tier="premium",
            expected_locked_at=old.claim_locked_at,
        ))
        self.assertFalse(complete_ai_job(
            DjangoUnitOfWork(), job.job_id, {"winner": "old"},
            expected_locked_at=old.claim_locked_at,
        ))
        job.refresh_from_db()
        self.assertEqual(job.status, "FAILED")
        self.assertEqual(job.error_message, "new_error")
        self.assertFalse(AIResultModel.objects.filter(job=job).exists())
