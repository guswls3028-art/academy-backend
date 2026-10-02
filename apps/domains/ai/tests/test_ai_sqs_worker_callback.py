from __future__ import annotations

from unittest import mock

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase

from academy.framework.workers import ai_sqs_worker
from apps.core.models import Tenant
from apps.core.models.user import user_internal_username
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

    def test_wrong_note_preflight_tenant_poison_is_deleted_without_domain_write(self):
        Student = django_apps.get_model("students", "Student")
        Lecture = django_apps.get_model("lectures", "Lecture")
        Enrollment = django_apps.get_model("enrollment", "Enrollment")
        WrongNotePDF = django_apps.get_model("results", "WrongNotePDF")
        source_user = get_user_model().objects.create_user(
            username=user_internal_username(self.tenant, "WN001"),
            password="test1234",
            tenant=self.tenant,
        )
        student = Student.objects.create(
            tenant=self.tenant,
            user=source_user,
            ps_number="WN001",
            name="Wrong Note Source",
            omr_code="000WN001",
            parent_phone="010-3333-0001",
        )
        lecture = Lecture.objects.create(
            tenant=self.tenant, title="Wrong Note", name="Wrong Note", subject="MATH",
        )
        enrollment = Enrollment.objects.create(
            tenant=self.tenant, student=student, lecture=lecture, status="ACTIVE",
        )
        pdf_job = WrongNotePDF.objects.create(enrollment=enrollment, status="PENDING")
        other_tenant = Tenant.objects.create(
            name="Wrong Note Boundary", code="ai-cb-boundary", is_active=True,
        )
        job = AIJobModel.objects.create(
            job_id="wrong-note-preflight-tenant-poison",
            job_type="wrong_note_pdf_generation",
            status="PENDING",
            tenant_id=str(other_tenant.id),
            tier="basic",
            source_domain="results_wrong_note_pdf",
            source_id=str(pdf_job.id),
            payload={"wrong_note_pdf_job_id": pdf_job.id},
        )
        message = self._message_for(job)
        message["source_domain"] = job.source_domain
        message["source_id"] = job.source_id
        queue = _OneMessageQueue(message)

        with (
            mock.patch("apps.domains.ai.callbacks.dispatch_ai_result_to_domain") as dispatch,
            self.assertLogs("academy.ai_sqs_worker", level="INFO") as logs,
        ):
            exit_code = ai_sqs_worker.run_ai_sqs_worker(
                queue=queue,
                inference_handler=mock.Mock(
                    side_effect=AssertionError("poison job must not run inference")
                ),
            )

        job.refresh_from_db()
        pdf_job.refresh_from_db()
        self.assertEqual(exit_code, 0)
        self.assertTrue(queue.deleted)
        self.assertEqual(job.status, "FAILED")
        self.assertEqual(job.error_message, "tenant_mismatch_in_sqs_message")
        self.assertEqual(pdf_job.status, WrongNotePDF.Status.PENDING)
        self.assertEqual(pdf_job.file_path, "")
        self.assertEqual(pdf_job.error_message, "")
        dispatch.assert_not_called()
        self.assertEqual(queue.delete_calls, 1)
        self.assertIn("AI_JOB_SQS_ACK | job_id=wrong-note-preflight-tenant-poison", "\n".join(logs.output))


    def test_wrong_note_terminal_failure_still_retries_domain_callback(self):
        job = AIJobModel.objects.create(
            job_id="wrong-note-terminal-failure-retry",
            job_type="wrong_note_pdf_generation",
            status="FAILED",
            tenant_id=str(self.tenant.id),
            tier="basic",
            source_domain="results_wrong_note_pdf",
            source_id="123",
            error_message="render_failed",
            last_error="render_failed",
        )
        message = self._message_for(job)
        message["source_domain"] = job.source_domain
        message["source_id"] = job.source_id
        queue = _OneMessageQueue(message)

        with mock.patch(
            "apps.domains.ai.callbacks.dispatch_ai_result_to_domain",
            return_value=False,
        ) as dispatch:
            exit_code = ai_sqs_worker.run_ai_sqs_worker(queue=queue)

        self.assertEqual(exit_code, 0)
        self.assertFalse(queue.deleted)
        dispatch.assert_called_once_with(
            job_id=job.job_id,
            status="FAILED",
            result_payload={},
            error="render_failed",
            source_domain=job.source_domain,
            source_id=job.source_id,
            tier="basic",
        )


    def test_live_claim_redelivery_is_retained_without_inference_or_callback(self):
        from datetime import timedelta
        from django.utils import timezone

        claim = timezone.now()
        job = AIJobModel.objects.create(
            job_id="live-claim-redelivery", job_type="ocr", status="RUNNING",
            tenant_id=str(self.tenant.id), tier="basic", source_domain="matchup",
            source_id="123", locked_at=claim, locked_by="current-worker",
            lease_expires_at=claim + timedelta(minutes=30),
        )
        queue = _OneMessageQueue(self._message_for(job))
        inference = mock.Mock(side_effect=AssertionError("live claim must not rerun"))
        with mock.patch("apps.domains.ai.callbacks.dispatch_ai_result_to_domain") as dispatch:
            code = ai_sqs_worker.run_ai_sqs_worker(queue=queue, inference_handler=inference)
        self.assertEqual(code, 0)
        job.refresh_from_db()
        self.assertEqual(job.status, "RUNNING")
        self.assertEqual(job.locked_at, claim)
        self.assertEqual(job.locked_by, "current-worker")
        self.assertFalse(queue.deleted)
        self.assertEqual(queue.delete_calls, 0)
        inference.assert_not_called()
        dispatch.assert_not_called()


class AISQSTerminalReadbackTests(TransactionTestCase):
    setUp = AISQSWorkerCallbackTests.setUp
    tearDown = AISQSWorkerCallbackTests.tearDown
    _message_for = AISQSWorkerCallbackTests._message_for

    def test_handler_committed_terminal_result_uses_persisted_winner_and_ack(self):
        from academy.adapters.db.django.repositories_ai import DjangoAIJobRepository
        from django.utils import timezone

        job = AIJobModel.objects.create(
            job_id="inline-excel-completion", job_type="excel_parsing",
            status="PENDING", tenant_id=str(self.tenant.id), tier="basic",
            source_domain="matchup", source_id="123",
        )
        message = self._message_for(job)
        message["queue_name"] = "academy-v1-development-tools-queue"
        queue = _OneMessageQueue(message)

        def handler(contract):
            self.assertTrue(DjangoAIJobRepository().mark_done(
                job.job_id, timezone.now(), {"winner": "committed"},
            ))
            ai_sqs_worker._shutdown = True
            return AIResult.done(contract.id, {"winner": "returned-later"})

        with mock.patch("apps.domains.ai.callbacks.dispatch_ai_result_to_domain") as dispatch:
            code = ai_sqs_worker.run_ai_sqs_worker(
                queue=queue, inference_handler=handler, worker_kind="tools",
            )
        self.assertEqual(code, 0)
        job.refresh_from_db()
        self.assertEqual(job.status, "DONE")
        self.assertEqual(AIResultModel.objects.get(job=job).payload, {"winner": "committed"})
        dispatch.assert_called_once()
        self.assertEqual(dispatch.call_args.kwargs["result_payload"], {"winner": "committed"})
        self.assertTrue(queue.deleted)
        self.assertEqual(queue.delete_calls, 1)
