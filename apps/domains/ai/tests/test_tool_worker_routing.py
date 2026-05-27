from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from academy.adapters.db.django.repositories_ai import DjangoAIJobRepository
from apps.domains.ai.models import AIJobModel
from apps.domains.ai.queueing.publisher import publish_ai_job_sqs


class ToolWorkerRoutingTests(TestCase):
    def _job(self, job_type: str) -> AIJobModel:
        return AIJobModel.objects.create(
            job_id=str(uuid.uuid4()),
            job_type=job_type,
            status="PENDING",
            tenant_id="1",
            payload={"tenant_id": "1"},
            tier="basic",
            source_domain="tools",
        )

    @override_settings(TOOLS_SQS_QUEUE_NAME="test-tools-queue")
    @patch("apps.support.ai.services.sqs_queue.get_queue_client")
    def test_ppt_generation_publishes_to_tools_queue(self, get_queue_client):
        client = Mock()
        client.send_message.return_value = True
        get_queue_client.return_value = client

        publish_ai_job_sqs(self._job("ppt_generation"))

        client.send_message.assert_called_once()
        assert client.send_message.call_args.kwargs["queue_name"] == "test-tools-queue"

    @patch("apps.support.ai.services.sqs_queue.get_queue_client")
    def test_non_tool_job_stays_on_ai_queue(self, get_queue_client):
        client = Mock()
        client.send_message.return_value = True
        get_queue_client.return_value = client

        publish_ai_job_sqs(self._job("ocr"))

        client.send_message.assert_called_once()
        assert client.send_message.call_args.kwargs["queue_name"] == "test-ai-queue"

    def test_ai_job_execution_timestamps_are_preserved_after_completion(self):
        job = self._job("ppt_generation")
        repo = DjangoAIJobRepository()
        started = timezone.now()

        assert repo.mark_running(
            job.job_id,
            "tools-sqs-worker",
            started + timedelta(minutes=30),
            started,
        )
        assert repo.mark_done(job.job_id, timezone.now(), {"slide_count": 1})

        job.refresh_from_db()
        assert job.started_at is not None
        assert job.completed_at is not None
        assert job.started_at <= job.completed_at

    def test_completed_at_is_backfilled_for_idempotent_done_mark(self):
        job = self._job("ppt_generation")
        job.status = "DONE"
        job.completed_at = None
        job.save(update_fields=["status", "completed_at", "updated_at"])

        assert DjangoAIJobRepository().mark_done(job.job_id, timezone.now(), {"slide_count": 1})

        job.refresh_from_db()
        assert job.completed_at is not None
