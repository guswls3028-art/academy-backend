from __future__ import annotations

from unittest import mock

from django.test import TestCase

from academy.framework.workers import ai_sqs_worker
from apps.core.models import Tenant
from apps.domains.ai.models import AIJobModel, AIResultModel
from apps.shared.contracts.ai_result import AIResult


class _OneMessageQueue:
    def __init__(self, message: dict, *, delete_result: bool = True, delete_error=None):
        self._message = message
        self.deleted = False
        self.delete_result = delete_result
        self.delete_error = delete_error
        self.delete_calls = 0

    def receive(self, *, tier: str, wait_time_seconds: int):
        message = self._message
        self._message = None
        if message is None:
            ai_sqs_worker._shutdown = True
        return message

    def delete(self, receipt_handle: str, tier: str) -> bool:
        self.delete_calls += 1
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted = self.delete_result
        return self.delete_result

    def extend_visibility(self, receipt_handle: str, tier: str, timeout: int) -> bool:
        return True


class AISQSWorkerCallbackTests(TestCase):
    def setUp(self):
        self.close_old_connections_patcher = mock.patch.object(
            ai_sqs_worker,
            "close_old_connections",
        )
        self.close_old_connections = self.close_old_connections_patcher.start()
        self.addCleanup(self.close_old_connections_patcher.stop)
        self.release_connections_patcher = mock.patch.object(
            ai_sqs_worker,
            "_release_db_connections",
        )
        self.release_connections = self.release_connections_patcher.start()
        self.addCleanup(self.release_connections_patcher.stop)
        self.tenant = Tenant.objects.create(name="AI Callback", code="ai-cb", is_active=True)

    def tearDown(self):
        ai_sqs_worker._shutdown = False
        ai_sqs_worker._current_receipt_handle = None

    def _message_for(self, job: AIJobModel) -> dict:
        return {
            "receipt_handle": f"rh-{job.job_id}",
            "message_id": f"mid-{job.job_id}",
            "queue_name": "academy-v1-development-ai-queue",
            "job_id": job.job_id,
            "job_type": job.job_type,
            "tier": job.tier,
            "tenant_id": str(self.tenant.id),
            "source_domain": "matchup",
            "source_id": "123",
            "payload": {},
        }

    def test_callback_failure_keeps_terminal_result_and_message_for_retry(self):
        job = AIJobModel.objects.create(
            job_id="callback-failure",
            job_type="ocr",
            status="PENDING",
            tenant_id=str(self.tenant.id),
            tier="basic",
        )
        queue = _OneMessageQueue(self._message_for(job))

        def inference_handler(contract_job):
            ai_sqs_worker._shutdown = True
            return AIResult.done(contract_job.id, {"ok": True})

        with mock.patch(
            "apps.domains.ai.callbacks.dispatch_ai_result_to_domain",
            side_effect=RuntimeError("domain write failed"),
        ):
            exit_code = ai_sqs_worker.run_ai_sqs_worker(
                queue=queue,
                inference_handler=inference_handler,
            )

        job.refresh_from_db()
        self.assertEqual(exit_code, 0)
        self.assertFalse(queue.deleted)
        self.assertEqual(job.status, "DONE")
        self.assertTrue(AIResultModel.objects.filter(job=job, payload={"ok": True}).exists())

    def test_terminal_redelivery_retries_callback_and_deletes_message(self):
        job = AIJobModel.objects.create(
            job_id="callback-redelivery",
            job_type="ocr",
            status="DONE",
            tenant_id=str(self.tenant.id),
            tier="basic",
            source_domain="matchup",
            source_id="123",
        )
        AIResultModel.objects.create(job=job, payload={"ok": True})
        queue = _OneMessageQueue(self._message_for(job))

        inference_handler = mock.Mock(side_effect=AssertionError("terminal job must not rerun inference"))
        with self.assertLogs("academy.ai_sqs_worker", level="INFO") as logs:
            with mock.patch("apps.domains.ai.callbacks.dispatch_ai_result_to_domain") as dispatch:
                exit_code = ai_sqs_worker.run_ai_sqs_worker(
                    queue=queue,
                    inference_handler=inference_handler,
                )

        self.assertEqual(exit_code, 0)
        self.assertTrue(queue.deleted)
        self.assertEqual(queue.delete_calls, 1)
        self.assertEqual(
            [line for line in logs.output if "AI_JOB_SQS_ACK |" in line],
            ["INFO:academy.ai_sqs_worker:AI_JOB_SQS_ACK | job_id=callback-redelivery | "
             "queue=academy-v1-development-ai-queue | message_id=mid-callback-redelivery"],
        )
        inference_handler.assert_not_called()
        dispatch.assert_called_once_with(
            job_id="callback-redelivery",
            status="DONE",
            result_payload={"ok": True},
            error=None,
            source_domain="matchup",
            source_id="123",
            tier="basic",
        )

    def test_callback_success_completes_and_deletes_message(self):
        job = AIJobModel.objects.create(
            job_id="callback-success",
            job_type="ocr",
            status="PENDING",
            tenant_id=str(self.tenant.id),
            tier="basic",
        )
        queue = _OneMessageQueue(self._message_for(job))

        def inference_handler(contract_job):
            ai_sqs_worker._shutdown = True
            return AIResult.done(contract_job.id, {"ok": True})

        with self.assertLogs("academy.ai_sqs_worker", level="INFO") as logs:
            with mock.patch("apps.domains.ai.callbacks.dispatch_ai_result_to_domain"):
                exit_code = ai_sqs_worker.run_ai_sqs_worker(
                    queue=queue,
                    inference_handler=inference_handler,
                )

        job.refresh_from_db()
        self.assertEqual(exit_code, 0)
        self.assertTrue(queue.deleted)
        self.assertEqual(queue.delete_calls, 1)
        self.assertEqual(
            [line for line in logs.output if "AI_JOB_SQS_ACK |" in line],
            ["INFO:academy.ai_sqs_worker:AI_JOB_SQS_ACK | job_id=callback-success | "
             "queue=academy-v1-development-ai-queue | message_id=mid-callback-success"],
        )
        self.assertEqual(job.status, "DONE")
        self.assertTrue(AIResultModel.objects.filter(job=job, payload={"ok": True}).exists())

    def test_completed_delete_false_does_not_log_ack(self):
        job = AIJobModel.objects.create(
            job_id="completed-delete-false", job_type="ocr", status="PENDING",
            tenant_id=str(self.tenant.id), tier="basic",
        )
        queue = _OneMessageQueue(self._message_for(job), delete_result=False)

        def inference_handler(contract_job):
            ai_sqs_worker._shutdown = True
            return AIResult.done(contract_job.id, {"ok": True})

        with self.assertLogs("academy.ai_sqs_worker", level="INFO") as logs:
            exit_code = ai_sqs_worker.run_ai_sqs_worker(
                queue=queue, inference_handler=inference_handler,
            )

        job.refresh_from_db()
        self.assertEqual(exit_code, 0)
        self.assertEqual(job.status, "DONE")
        self.assertEqual(queue.delete_calls, 1)
        self.assertFalse(queue.deleted)
        self.assertFalse(any("AI_JOB_SQS_ACK |" in line for line in logs.output))
        self.assertTrue(any("AI_JOB_SQS_DELETE_FAILED" in line for line in logs.output))
        self.assertTrue(any("SQS_JOB_COMPLETED" in line for line in logs.output))

    def test_completed_delete_exception_does_not_log_ack(self):
        job = AIJobModel.objects.create(
            job_id="completed-delete-exception", job_type="ocr", status="PENDING",
            tenant_id=str(self.tenant.id), tier="basic",
        )
        queue = _OneMessageQueue(self._message_for(job), delete_error=RuntimeError("SQS unavailable"))

        def inference_handler(contract_job):
            ai_sqs_worker._shutdown = True
            return AIResult.done(contract_job.id, {"ok": True})

        with self.assertLogs("academy.ai_sqs_worker", level="INFO") as logs:
            exit_code = ai_sqs_worker.run_ai_sqs_worker(
                queue=queue, inference_handler=inference_handler,
            )

        job.refresh_from_db()
        self.assertEqual(exit_code, 0)
        self.assertEqual(job.status, "DONE")
        self.assertEqual(queue.delete_calls, 1)
        self.assertFalse(any("AI_JOB_SQS_ACK |" in line for line in logs.output))
        self.assertTrue(any("AI_JOB_SQS_DELETE_FAILED" in line for line in logs.output))

    def test_terminal_redelivery_delete_false_does_not_log_ack(self):
        job = AIJobModel.objects.create(
            job_id="redelivery-delete-false", job_type="ocr", status="DONE",
            tenant_id=str(self.tenant.id), tier="basic",
            source_domain="matchup", source_id="123",
        )
        AIResultModel.objects.create(job=job, payload={"ok": True})
        queue = _OneMessageQueue(self._message_for(job), delete_result=False)
        inference_handler = mock.Mock(side_effect=AssertionError("must not rerun inference"))

        with self.assertLogs("academy.ai_sqs_worker", level="INFO") as logs:
            exit_code = ai_sqs_worker.run_ai_sqs_worker(
                queue=queue, inference_handler=inference_handler,
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(queue.delete_calls, 1)
        self.assertFalse(queue.deleted)
        inference_handler.assert_not_called()
        self.assertFalse(any("AI_JOB_SQS_ACK |" in line for line in logs.output))
        self.assertTrue(any("AI_JOB_SQS_DELETE_FAILED" in line for line in logs.output))
