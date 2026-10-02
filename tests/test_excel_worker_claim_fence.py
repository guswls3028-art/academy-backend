from __future__ import annotations

import threading
import unittest
import uuid
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from queue import Queue
from unittest.mock import Mock, patch

from django.db import connection, connections
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.utils import timezone

from academy.adapters.db.django.uow import DjangoUnitOfWork
from academy.application.services.excel_parsing_service import ExcelParsingService
from academy.application.use_cases.ai.pipelines.excel_handler import handle_excel_parsing_job
from academy.application.use_cases.ai.process_ai_job_from_sqs import prepare_ai_job
from academy.framework.workers.ai_sqs_worker import _to_contract_job
from apps.core.models import Tenant
from apps.domains.ai.models import AIJobModel, AIResultModel
from apps.domains.students.models import Student
from apps.shared.contracts.ai_job import AIJob


class ExcelClaimWireTests(SimpleTestCase):
    def test_envelope_cannot_supply_or_serialize_runtime_claim(self):
        stamp = timezone.now()
        envelope = {"id": "claim-envelope", "type": "excel_parsing", "tenant_id": "1", "payload": {}, "claim_locked_at": stamp.isoformat()}
        parsed = AIJob.from_dict(envelope)
        self.assertIsNone(parsed.claim_locked_at)
        trusted = replace(parsed, claim_locked_at=stamp)
        self.assertNotIn("claim_locked_at", trusted.to_dict())
        self.assertNotIn("claim_locked_at", trusted.to_json())
        self.assertIsNone(AIJob.from_json(trusted.to_json()).claim_locked_at)


class _ExcelClaimFixtures:
    def setUp(self):
        super().setUp()
        self.tenant = Tenant.objects.create(name="QA Excel Claim", code="qa-excel-claim")
        self.storage = Mock()
        self.rows = [{"name": "QA Claim Student", "parent_phone": "01090001001", "phone": None, "school": "QA High", "school_type": "HIGH", "grade": 1, "_excel_row": 2}]
        self.payload = {"tenant_id": str(self.tenant.id), "file_key": f"tenants/{self.tenant.id}/excel/qa-claim.xlsx", "initial_password_mode": "random", "parent_initial_password_mode": "phone_last4"}

    def _job(self):
        started = timezone.now()
        job = AIJobModel.objects.create(job_id=str(uuid.uuid4()), job_type="excel_parsing", status="PENDING", tenant_id=str(self.tenant.id), tier="basic", source_domain="tools", payload=self.payload)
        prepared = prepare_ai_job(DjangoUnitOfWork(), job_id=job.job_id, receipt_handle="old", tier="basic", payload=self.payload, job_type=job.job_type, tenant_id=str(self.tenant.id), source_domain="tools", worker_id="qa-tools", lease_seconds=60, now=started)
        self.assertIsNotNone(prepared)
        return job, prepared, started

    def _parser(self):
        return patch("academy.application.services.excel_parsing_service.parse_student_excel_file", return_value=(self.rows, ""))


class ExcelWorkerClaimTests(_ExcelClaimFixtures, TestCase):
    def test_valid_claim_registers_real_account_graph_pending_notice_and_encrypted_result(self):
        job, prepared, _ = self._job()
        self.assertEqual(_to_contract_job(prepared).claim_locked_at, prepared.claim_locked_at)
        with self._parser():
            result = ExcelParsingService(self.storage).run(job.job_id, self.payload, expected_locked_at=prepared.claim_locked_at)
        self.assertEqual(result.get("failed"), [], result.get("failed"))
        student = Student.objects.get(tenant=self.tenant)
        self.assertEqual(student.name, "QA Claim Student")
        self.assertEqual(student.user.tenant_id, self.tenant.id)
        self.assertEqual(student.parent.tenant_id, self.tenant.id)
        self.assertTrue(student.pending_account_notice_student_password_ciphertext)
        self.assertEqual(student.pending_account_notice_origin_id, job.job_id)
        job.refresh_from_db()
        self.assertEqual(job.status, "DONE")
        self.assertIsNone(job.locked_at)
        stored = AIResultModel.objects.get(job=job).payload
        self.assertNotIn("credentials", stored)
        self.assertNotIn("initial_password", job.payload)
        self.assertEqual(result["created"], 1)
        self.assertFalse(Path(self.storage.download_to_path.call_args.args[2]).exists())

    def test_old_handler_preserves_input_and_new_claim_can_finish_normally(self):
        job, old, started = self._job()
        new = prepare_ai_job(DjangoUnitOfWork(), job_id=job.job_id, receipt_handle="new", tier="basic", payload=self.payload, job_type=job.job_type, tenant_id=str(self.tenant.id), source_domain="tools", worker_id="qa-tools-new", lease_seconds=60, now=started + timedelta(seconds=61))
        self.assertIsNotNone(new)
        with self._parser(), patch("academy.application.use_cases.ai.pipelines.excel_handler.R2ObjectStorageAdapter", return_value=self.storage), patch("academy.application.use_cases.ai.pipelines.excel_handler._record_progress"):
            rejected = handle_excel_parsing_job(_to_contract_job(old))
            self.assertEqual(rejected.status, "FAILED")
            self.assertEqual(Student.objects.filter(tenant=self.tenant).count(), 0)
            self.assertFalse(AIResultModel.objects.filter(job=job).exists())
            job.refresh_from_db()
            self.assertEqual(job.status, "RUNNING")
            self.assertEqual(job.locked_at, new.claim_locked_at)
            self.storage.delete_object.assert_not_called()
            completed = handle_excel_parsing_job(_to_contract_job(new))
        self.assertEqual(completed.status, "DONE", completed.error)
        self.assertEqual(completed.result.get("failed"), [], completed.result.get("failed"))
        self.assertEqual(Student.objects.filter(tenant=self.tenant).count(), 1)
        self.storage.delete_object.assert_called_once()
        paths = [call.args[2] for call in self.storage.download_to_path.call_args_list]
        self.assertEqual(len(set(paths)), 2)
        self.assertTrue(all(not Path(path).exists() for path in paths))
        job.refresh_from_db()
        self.assertEqual(job.status, "DONE")

    def test_wrong_tenant_cannot_enter_registration_transaction(self):
        job, prepared, _ = self._job()
        foreign = Tenant.objects.create(name="QA Foreign", code="qa-excel-foreign")
        payload = {**self.payload, "tenant_id": str(foreign.id)}
        with self._parser(), patch("apps.domains.students.services.import_students_from_rows") as register:
            with self.assertRaisesRegex(RuntimeError, "excel_job_claim_changed"):
                ExcelParsingService(self.storage).run(job.job_id, payload, expected_locked_at=prepared.claim_locked_at)
        register.assert_not_called()
        self.assertEqual(Student.objects.count(), 0)
        job.refresh_from_db()
        self.assertEqual(job.status, "RUNNING")
        self.assertEqual(job.locked_at, prepared.claim_locked_at)


class ExcelClaimPostgresTests(_ExcelClaimFixtures, TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if connection.vendor != "postgresql":
            raise unittest.SkipTest("Real PostgreSQL is required for concurrent Excel claim verification.")

    @patch("apps.domains.ai.redis_status_cache.cache_job_status", return_value=True)
    def test_reclaimed_lease_before_old_registration_preserves_new_winner(self, _cache):
        job, old, started = self._job()
        parsing = threading.Event()
        proceed = threading.Event()
        outcomes = Queue()

        def paused_parse(*args, **kwargs):
            if threading.current_thread().name == "old-excel-claim":
                parsing.set()
                if not proceed.wait(10):
                    raise AssertionError("new claim was not released")
            return self.rows, ""

        def old_execution():
            try:
                ExcelParsingService(self.storage).run(job.job_id, self.payload, expected_locked_at=old.claim_locked_at)
                outcomes.put("unexpected-registration")
            except RuntimeError as exc:
                outcomes.put(str(exc))
            except BaseException as exc:
                outcomes.put(exc)
            finally:
                connections.close_all()

        with patch("academy.application.services.excel_parsing_service.parse_student_excel_file", side_effect=paused_parse):
            thread = threading.Thread(target=old_execution, name="old-excel-claim")
            thread.start()
            try:
                self.assertTrue(parsing.wait(10))
                new = prepare_ai_job(DjangoUnitOfWork(), job_id=job.job_id, receipt_handle="new", tier="basic", payload=self.payload, job_type=job.job_type, tenant_id=str(self.tenant.id), source_domain="tools", worker_id="qa-tools-new", lease_seconds=60, now=started + timedelta(seconds=61))
                self.assertIsNotNone(new)
            finally:
                proceed.set()
                thread.join(10)
            self.assertFalse(thread.is_alive())
            self.assertEqual(outcomes.get(timeout=1), "excel_job_claim_changed")
            self.assertEqual(Student.objects.filter(tenant=self.tenant).count(), 0)
            self.assertFalse(AIResultModel.objects.filter(job=job).exists())
            result = ExcelParsingService(self.storage).run(job.job_id, self.payload, expected_locked_at=new.claim_locked_at)
        self.assertEqual(result["created"], 1)
        self.assertEqual(Student.objects.filter(tenant=self.tenant).count(), 1)
        job.refresh_from_db()
        self.assertEqual(job.status, "DONE")
        self.assertEqual(AIResultModel.objects.filter(job=job).count(), 1)