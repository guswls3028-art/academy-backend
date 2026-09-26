from __future__ import annotations

from unittest import mock

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase

from academy.framework.workers import ai_sqs_worker
from apps.core.models import Tenant
from apps.core.models.user import user_internal_username
from apps.domains.ai.models import AIJobModel, AIResultModel
from apps.shared.contracts.ai_result import AIResult


class _OneMessageQueue:
    def __init__(self, message: dict, *, delete_succeeds: bool = True):
        self._message = message
        self.deleted = False
        self.delete_succeeds = delete_succeeds

    def receive(self, *, tier: str, wait_time_seconds: int):
        message = self._message
        self._message = None
        if message is None:
            ai_sqs_worker._shutdown = True
        return message

    def delete(self, receipt_handle: str, tier: str) -> bool:
        self.deleted = True
        return self.delete_succeeds

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
        with mock.patch("apps.domains.ai.callbacks.dispatch_ai_result_to_domain") as dispatch:
            exit_code = ai_sqs_worker.run_ai_sqs_worker(
                queue=queue,
                inference_handler=inference_handler,
            )

        self.assertEqual(exit_code, 0)
        self.assertTrue(queue.deleted)
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

    def test_terminal_redelivery_reports_message_delete_failure(self):
        job = AIJobModel.objects.create(
            job_id="callback-redelivery-delete-failure",
            job_type="ocr",
            status="DONE",
            tenant_id=str(self.tenant.id),
            tier="basic",
            source_domain="matchup",
            source_id="123",
        )
        AIResultModel.objects.create(job=job, payload={"ok": True})
        queue = _OneMessageQueue(
            self._message_for(job),
            delete_succeeds=False,
        )

        with (
            mock.patch("apps.domains.ai.callbacks.dispatch_ai_result_to_domain"),
            self.assertLogs("academy.ai_sqs_worker", level="INFO") as logs,
        ):
            exit_code = ai_sqs_worker.run_ai_sqs_worker(queue=queue)

        self.assertEqual(exit_code, 0)
        self.assertTrue(queue.deleted)
        rendered = "\n".join(logs.output)
        self.assertIn(
            "AI_JOB_IDEMPOTENT_DELETE_FAILED | "
            "job_id=callback-redelivery-delete-failure",
            rendered,
        )
        self.assertIn("message_deleted=false", rendered)

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
        self.assertIn("callback_ok=true message_deleted=true", "\n".join(logs.output))

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

        with mock.patch("apps.domains.ai.callbacks.dispatch_ai_result_to_domain"):
            exit_code = ai_sqs_worker.run_ai_sqs_worker(
                queue=queue,
                inference_handler=inference_handler,
            )

        job.refresh_from_db()
        self.assertEqual(exit_code, 0)
        self.assertTrue(queue.deleted)
        self.assertEqual(job.status, "DONE")
        self.assertTrue(AIResultModel.objects.filter(job=job, payload={"ok": True}).exists())

    def test_basic_failure_callback_uses_persisted_status_and_error(self):
        job = AIJobModel.objects.create(
            job_id="callback-basic-failure",
            job_type="ocr",
            status="PENDING",
            tenant_id=str(self.tenant.id),
            tier="basic",
        )
        queue = _OneMessageQueue(self._message_for(job))

        def inference_handler(contract_job):
            ai_sqs_worker._shutdown = True
            return AIResult.failed(contract_job.id, "provider-timeout")

        with mock.patch(
            "apps.domains.ai.callbacks.dispatch_ai_result_to_domain"
        ) as dispatch:
            exit_code = ai_sqs_worker.run_ai_sqs_worker(
                queue=queue,
                inference_handler=inference_handler,
            )

        job.refresh_from_db()
        self.assertEqual(exit_code, 0)
        self.assertTrue(queue.deleted)
        self.assertEqual(job.status, "DONE")
        self.assertEqual(job.error_message, "provider-timeout")
        dispatch.assert_called_once_with(
            job_id=job.job_id,
            status=job.status,
            result_payload={},
            error=job.error_message,
            source_domain="matchup",
            source_id="123",
            tier="basic",
        )
