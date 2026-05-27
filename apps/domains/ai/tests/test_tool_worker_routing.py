from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from academy.adapters.db.django.repositories_ai import DjangoAIJobRepository
from academy.application.use_cases.ai.process_ai_job_from_sqs import PreparedJob
from academy.application.use_cases.ai.pipelines.tier_enforcer import enforce_tier_limits
from academy.application.use_cases.tools.worker_dispatcher import handle_tools_job
from academy.framework.workers.ai_sqs_worker import _run_inference
from apps.domains.ai.models import AIJobModel
from apps.domains.ai.queueing.publisher import publish_ai_job_sqs
from apps.shared.contracts.ai_result import AIResult


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
    def test_deterministic_document_jobs_publish_to_tools_queue(self, get_queue_client):
        for job_type in (
            "ppt_generation",
            "excel_parsing",
            "attendance_excel_export",
            "staff_excel_export",
        ):
            with self.subTest(job_type=job_type):
                client = Mock()
                client.send_message.return_value = True
                get_queue_client.return_value = client

                publish_ai_job_sqs(self._job(job_type))

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

    def test_tools_inference_handler_bypasses_ai_dispatcher_imports(self):
        prepared = PreparedJob(
            job_id=str(uuid.uuid4()),
            job_type="excel_parsing",
            tier="basic",
            payload={"tenant_id": "1", "file_key": "excel/1/test.xlsx"},
            receipt_handle="receipt",
            tenant_id="1",
            source_domain="tools",
        )

        real_import = __import__

        def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "academy.application.use_cases.ai.pipelines.dispatcher":
                raise AssertionError("tools worker must not import AI dispatcher")
            return real_import(name, globals, locals, fromlist, level)

        with (
            patch("builtins.__import__", guarded_import),
            patch(
                "academy.application.use_cases.ai.pipelines.excel_handler.handle_excel_parsing_job",
                return_value=AIResult.done(prepared.job_id, {"ok": True}),
            ),
        ):
            result = _run_inference(
                prepared,
                inference_handler=handle_tools_job,
            )

        assert result.status == "DONE"
        assert result.result == {"ok": True}

    def test_tools_dispatcher_rejects_unknown_job_type(self):
        prepared = PreparedJob(
            job_id=str(uuid.uuid4()),
            job_type="unknown_tool",
            tier="premium",
            payload={"tenant_id": "1"},
            receipt_handle="receipt",
            tenant_id="1",
            source_domain="tools",
        )

        result = _run_inference(prepared, inference_handler=handle_tools_job)

        assert result.status == "FAILED"
        assert "Unsupported tools job type" in (result.error or "")

    def test_basic_tier_allows_excel_exports(self):
        for job_type in ("attendance_excel_export", "staff_excel_export"):
            with self.subTest(job_type=job_type):
                allowed, error = enforce_tier_limits(tier="basic", job_type=job_type)

                assert allowed
                assert error is None
