from hashlib import sha256
import os
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.core.management.base import CommandError
from django.db import OperationalError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from PIL import Image

from apps.core.services.development_qa_ownership import WRONG_NOTE_SETUP_SCHEMA, verify_setup_owner
from apps.core.services.development_qa_ai_cleanup import OwnedQaAI
from apps.core.services.development_qa_queue_ack import ACTION, QUEUES, record_ack, require_acks
from apps.core.services.wrong_note_development_scenario import verify_pdf
from apps.core.models import OpsAuditLog, Tenant
from apps.domains.ai.models import AIJobModel
from apps.domains.results.services import wrong_note_pdf_service as pdf
from academy.framework.workers.ai_sqs_worker import _delete_with_ack


class WrongNoteDevelopmentScopeTests(SimpleTestCase):
    def test_wrong_note_seal_cannot_authorize_frontend_cleanup(self):
        code, tenant_id, token = "qa-ymath-realuse-wn-123-1-abcdef123456", 98, "b" * 64
        record = {
            "schema": WRONG_NOTE_SETUP_SCHEMA, "tenant_code": code, "tenant_id": tenant_id,
            "owner_sha256": sha256(f"{code}:{tenant_id}:{token}".encode()).hexdigest(),
        }
        with self.assertRaises(PermissionError):
            verify_setup_owner(code, tenant_id, token, record)
        with self.assertRaises(PermissionError):
            verify_setup_owner(code, tenant_id, "a" * 64, record, schema=WRONG_NOTE_SETUP_SCHEMA)
        self.assertEqual(verify_setup_owner(code, tenant_id, token, record, schema=WRONG_NOTE_SETUP_SCHEMA), record)

    def test_readback_distinguishes_students_and_answer_numbers_in_actual_pdf(self):
        numbers = [25, 4]
        def question_image(item, **kwargs):
            return Image.new("RGB", (70, 70), (item["question_number"], 0, 0))
        rows = [{"question_number": number, "question_id": number, "correct_answer": str((number - 1) % 5 + 1),
                 "session_order": i + 1, "exam_title": "합성 시험", "exam_id": i + 1} for i, number in enumerate(numbers)]
        with patch.object(pdf, "_load_question_image", side_effect=question_image):
            content = pdf.build_wrong_note_pdf(enrollment=SimpleNamespace(student=SimpleNamespace(name="검증학생"),
                                               lecture=SimpleNamespace(title="합성 강의")), tenant_name="합성",
                                               items=rows, from_session_order=1, to_session_order=2, exam_id=None)
        verify_pdf(content, "검증학생", numbers)
        with self.assertRaises(CommandError):
            verify_pdf(content, "다른학생", numbers)
        with self.assertRaises(CommandError):
            verify_pdf(content, "검증학생", [7, 2])


class WrongNoteQueueAckTests(TestCase):
    def test_retry_transient_evidence_failure_without_acknowledging_a_second_delivery(self):
        module = "apps.core.services.development_qa_queue_ack"
        with patch(f"{module}._record_ack", side_effect=[OperationalError(), None]) as write, \
                patch(f"{module}.close_old_connections"), patch(f"{module}.time.sleep"):
            record_ack({})
        self.assertEqual(write.call_count, 2)
        with patch(f"{module}._record_ack", side_effect=OperationalError()) as write, \
                patch(f"{module}.close_old_connections"), patch(f"{module}.time.sleep"):
            with self.assertRaises(OperationalError):
                record_ack({})
        self.assertEqual(write.call_count, 3)

    def test_only_exact_consumer_success_proves_owned_job_for_cleanup(self):
        release = f"sha-{'1' * 40}-run-123-1"
        tenant = Tenant.objects.create(name="QA", code="qa-ymath-realuse-fe-123-1-abcdef123456")
        seal = OpsAuditLog.objects.create(action="development.qa.setup", target_tenant=tenant, result="success",
                                          payload={"schema": "frontend-development-qa/v1", "owner_sha256": "a" * 64,
                                                   "tenant_code": tenant.code, "tenant_id": tenant.pk})
        job = AIJobModel.objects.create(job_id="wrong-note-pdf-71", tenant_id=str(tenant.pk), status="DONE",
                                        job_type="wrong_note_pdf_generation", completed_at=timezone.now())
        message = {"job_type": job.job_type, "job_id": job.job_id, "tenant_id": str(tenant.pk),
                   "queue_name": QUEUES["TOOLS"], "message_id": str(uuid4()), "receipt_handle": "test-only-handle"}
        env = {"ACADEMY_RUNTIME_ENV": "development", "DJANGO_SETTINGS_MODULE": "apps.api.config.settings.worker",
               "DB_NAME": "academy_api_development", "DB_USER": "academy_api_development_app",
               "ACADEMY_DEVELOPMENT_RELEASE_ID": release, "WORKER_TYPE": "TOOLS"}
        with patch.dict(os.environ, env):
            queue = Mock()
            queue.delete.return_value = False
            self.assertFalse(_delete_with_ack(queue, message, "tools"))
            queue.delete.return_value = True
            for bad in ({"queue_name": QUEUES["CPU"]}, {"message_id": "invalid"}, {"tenant_id": str(tenant.pk + 1)}):
                _delete_with_ack(queue, {**message, **bad}, "tools")
            with patch.dict(os.environ, {"ACADEMY_RUNTIME_ENV": "production"}):
                _delete_with_ack(queue, message, "tools")
            self.assertFalse(OpsAuditLog.objects.filter(action=ACTION).exists())
            with self.assertRaises(PermissionError):
                require_acks(seal, [job], wait_seconds=0)
            for _ in range(2):
                self.assertTrue(_delete_with_ack(queue, message, "tools"))
            require_acks(seal, [job], wait_seconds=0)
            scope = Mock(seal=seal)
            running = AIJobModel(job_id=job.job_id, job_type=job.job_type, status="RUNNING")
            with self.assertRaises(PermissionError):
                OwnedQaAI._drain_print_jobs(scope, [running], timeout=0)
            scope._jobs.return_value = [job]
            with patch("apps.core.services.development_qa_ai_cleanup.time.sleep"):
                self.assertEqual(OwnedQaAI._drain_print_jobs(scope, [running]), [job])
            audit = OpsAuditLog.objects.get(action=ACTION)
            self.assertEqual(audit.payload["message_id"], message["message_id"])
            self.assertNotIn("handle", str(audit.payload))
            with patch.dict(os.environ, {"ACADEMY_DEVELOPMENT_RELEASE_ID": f"sha-{'2' * 40}-run-124-1"}):
                with self.assertRaises(PermissionError):
                    require_acks(seal, [job], wait_seconds=0)
