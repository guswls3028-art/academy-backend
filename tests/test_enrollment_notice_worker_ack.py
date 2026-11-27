"""Synthetic receipts for the dedicated development messaging consumer."""
import json
import os
import secrets
import sys
from decimal import Decimal
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import OperationalError, connection
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from academy.adapters.db.django.repositories_messaging import (
    claim_notification_slot, finalize_notification, mark_notification_sending,
)
from apps.core.management.commands import setup_ymath_realuse_scenario as scenario
from apps.core.models import OpsAuditLog, Tenant, TenantMembership
from apps.core.services import enrollment_notice_development_scenario as qa
from apps.core.services.development_qa_ownership import WRONG_NOTE_SETUP_SCHEMA
from apps.core.tests.test_development_qa_ai_cleanup import MemoryRedis
from apps.domains.enrollment.models import EnrollmentNoticeRecipient, EnrollmentNoticeRegistration
from apps.domains.lectures.test_support import create_lecture_fixture
from apps.domains.messaging.models import AutoSendConfig, MessageTemplate, NotificationLog
from apps.domains.messaging.solapi_mock import MockSolapiMessageService
from apps.domains.messaging.sqs_queue import MessagingSQSQueue
from apps.domains.students.test_support import create_student_account_fixture
from apps.support.enrollment.notice_dispatch import process_registration_notices
from apps.worker.messaging_worker import qa_ack, sqs_main
from libs.queue.client import SQSQueueClient


class EnrollmentNoticeWorkerAckTests(TransactionTestCase):
    def setUp(self):
        self.owner = Tenant.objects.create(code="academy-development-owner", name="QA")
        self.tenant = Tenant.objects.create(code="qa-ymath-realuse-wn-123-1-aaaaaaaaaaaa", name="QA",
                                            account_password_policy={"parent_mode": "phone_last4"})
        self.lecture = create_lecture_fixture(tenant=self.tenant, title="QA", name="QA", subject="MATH")
        self.request_id = str(uuid4())
        self.release = "sha-" + "a" * 40 + "-run-123-1"
        self.env = {
            "ACADEMY_RUNTIME_ENV": "development", "ACADEMY_DEVELOPMENT_RELEASE_ID": self.release,
            "DJANGO_SETTINGS_MODULE": "apps.api.config.settings.worker",
            "DB_NAME": "academy_api_development", "DB_USER": "academy_api_development_app",
            "MESSAGING_SQS_QUEUE_NAME": qa_ack.QUEUE, "SOLAPI_MOCK": "true",
            "SOLAPI_KAKAO_PF_ID": "development-mock-pfid", "MESSAGING_DRY_RUN_TRIGGERS": "",
        }
        self.enterContext(patch.dict(os.environ, self.env))
        self.enterContext(patch.object(sys.modules["__main__"], "__spec__",
                                      SimpleNamespace(name="apps.worker.messaging_worker.sqs_main")))
        self.capability = secrets.token_hex(32)
        owner_hash = sha256(f"{self.tenant.code}:{self.tenant.pk}:{self.capability}".encode()).hexdigest()
        setup = OpsAuditLog.objects.create(
            action="development.wrong-note.setup", target_tenant=self.tenant, actor_username="wrong-note-development-runner",
            payload={"schema": WRONG_NOTE_SETUP_SCHEMA, "tenant_code": self.tenant.code, "tenant_id": self.tenant.pk,
                     "owner_sha256": owner_hash},
        )
        self.plan = OpsAuditLog.objects.create(
            action=qa_ack.PLAN, summary=self.request_id, target_tenant=self.tenant,
            actor_username="enrollment-notice-development-runner",
            payload={"schema": qa_ack.SCHEMA, "release": self.release, "request_id": self.request_id, "actor_id": 81,
                     "tenant_id": self.tenant.pk, "setup_id": setup.pk, "lecture_id": self.lecture.pk,
                     "student_ids": [71, 72], "redelivery_student_id": 71},
        )
        self.batch = EnrollmentNoticeRegistration.objects.create(
            tenant=self.tenant, lecture=self.lecture, actor_id=81, request_id=self.request_id, student_ids=[71, 72],
            student_notice="send", parent_notice="skip",
        )
        self.cfg = SimpleNamespace(OWNER_TENANT_ID=self.owner.pk, MESSAGING_SQS_QUEUE_NAME=qa_ack.QUEUE,
                                   SOLAPI_API_KEY="mock", SOLAPI_API_SECRET="mock", SOLAPI_SENDER="0212345678",
                                   SOLAPI_KAKAO_PF_ID="development-mock-pfid", TEST_TENANT_ID=9999, SQS_WAIT_TIME_SECONDS=0)
        self.queue = MagicMock()
        self.queue.delete_message.return_value = True
        self.queue.change_message_visibility.return_value = True

    def job(self, student_id=71, role="student"):
        recipient = EnrollmentNoticeRecipient.objects.create(registration=self.batch, role=role,
            student_id=student_id, status="admitted", outbox_id=student_id, business_key=str(uuid4()))
        data = {"domain_object_id": f"enrollment-notice:{recipient.pk}", "event_type": f"registration_approved_{role}",
                "target_type": "account", "target_id": f"{role}:{student_id}", "business_idempotency_key": recipient.business_key}
        raw = {"MessageId": str(uuid4()), "ReceiptHandle": "only-used-for-delete", "Attributes": {"ApproximateReceiveCount": "1"}}
        return raw, data

    def scope(self, raw, data):
        return qa_ack.enrollment_ack_scope(self.cfg, raw, data, self.owner.pk, self.tenant.pk)

    def send(self, raw, data):
        scope = self.scope(raw, data)
        claim = dict(tenant_id=self.owner.pk, source_tenant_id=self.tenant.pk, message_mode="alimtalk",
                     business_idempotency_key=data["business_idempotency_key"], sqs_message_id=raw["MessageId"],
                     target_type="account", target_id=data["target_id"])
        claimed, log_id = claim_notification_slot(**claim)
        self.assertTrue(claimed)
        phone = "01099000001" if data["event_type"] == "registration_approved_parent" else "01098000001"
        result = sqs_main.send_one_alimtalk(
            self.cfg, phone, "0212345678", "development-mock-pfid", "QA",
            before_provider_call=lambda: mark_notification_sending(log_id),
            observe_mock=lambda client: scope.observe_mock(client, log_id),
        )
        self.assertEqual(result["status"], "ok", result)
        finalize_notification(log_id, success=True, amount_deducted=Decimal("0"),
                              provider_message_id=result["group_id"], provider_reference_kind="group",
                              notification_type=data["event_type"])
        return scope, claim, log_id

    def ack(self, scope, log_id, deduplicated=False):
        return qa_ack.acknowledge_sent(self.queue, qa_ack.QUEUE, "current-handle", scope,
                                       log_id=log_id, deduplicated=deduplicated)

    def test_commit_same_sqs_envelope_redelivery_and_single_mock_call_per_student(self):
        raw, data = self.job()
        first, claim, log_id = self.send(raw, data)
        self.assertEqual(self.ack(first, log_id), (False, True))
        self.queue.delete_message.assert_not_called()
        self.queue.change_message_visibility.assert_called_once_with(
            queue_name=qa_ack.QUEUE, receipt_handle="current-handle", visibility_timeout=3)
        raw["Attributes"]["ApproximateReceiveCount"] = "2"
        self.assertEqual(claim_notification_slot(**claim), (False, None))
        self.assertEqual(self.ack(self.scope(raw, data), None, True), (True, False))

        other, _, other_log = self.send(*self.job(72))
        self.assertEqual(self.ack(other, other_log), (True, False))
        self.assertEqual(self.queue.delete_message.call_count, 2)
        self.assertEqual(self.queue.change_message_visibility.call_count, 1)
        events = OpsAuditLog.objects.filter(actor_username="MESSAGING", target_tenant=self.tenant,
                                            summary=self.request_id).order_by("pk")
        self.assertEqual(list(events.values_list("action", flat=True)),
                         [qa_ack.MOCK_SEND, qa_ack.REDELIVERY, qa_ack.ACK, qa_ack.MOCK_SEND, qa_ack.ACK])
        for event in events:
            p = event.payload
            saved = NotificationLog.objects.get(pk=p["log_id"], tenant=self.owner, source_tenant=self.tenant,
                                               sqs_message_id=p["message_id"], business_idempotency_key=p["business_key"])
            self.assertNotIn("ReceiptHandle", p)
            self.assertEqual(p["plan_id"], self.plan.pk)
            self.assertEqual(p["actor_id"], self.batch.actor_id)
            if event.action == qa_ack.MOCK_SEND:
                self.assertEqual(p["provider_message_id"], saved.provider_message_id)
            if event.action == qa_ack.ACK:
                self.assertEqual((p["receive_count"], p["deduplicated"]), (2, True) if saved.pk == log_id else (1, False))

    def test_failed_visibility_or_delete_cannot_issue_a_receipt(self):
        raw, data = self.job()
        first, _, log_id = self.send(raw, data)
        self.queue.change_message_visibility.return_value = False
        self.assertEqual(self.ack(first, log_id), (False, False))
        self.queue.delete_message.assert_not_called()
        raw["Attributes"]["ApproximateReceiveCount"] = "2"
        self.assertEqual(self.ack(self.scope(raw, data), None, True), (True, False))
        other, _, other_log = self.send(*self.job(72))
        self.queue.delete_message.return_value = False
        self.assertEqual(self.ack(other, other_log), (False, False))
        self.assertFalse(OpsAuditLog.objects.filter(action__in=[qa_ack.ACK, qa_ack.REDELIVERY]).exists())

    def test_parent_replay_after_false_delete_accepts_only_the_committed_parent_log(self):
        self.queue = self.queue_adapter()
        self.plan.payload["selected_role"] = "parent"
        self.plan.save(update_fields=["payload"])
        self.batch.student_notice, self.batch.parent_notice = "skip", "send"
        self.batch.save(update_fields=["student_notice", "parent_notice"])
        raw, data = self.job(role="parent")
        scope, claim, log_id = self.send(raw, data)
        self.assertEqual(self.ack(scope, log_id), (False, True))
        raw["Attributes"]["ApproximateReceiveCount"] = "2"
        replay = self.scope(raw, data)
        self.assertEqual(claim_notification_slot(**claim), (False, None))
        log = NotificationLog.objects.filter(pk=log_id)
        for wrong in ({"target_id": "student:71"}, {"notification_type": "registration_approved_student"},
                      {"tenant_id": self.tenant.pk}):
            old = log.values(*wrong).get()
            log.update(**wrong)
            with self.subTest(wrong=wrong), self.assertRaises(NotificationLog.DoesNotExist):
                self.ack(replay, None, True)
            log.update(**old)
        self.queue.sqs.delete_message.assert_not_called()
        self.queue.sqs.delete_message.side_effect = [OSError("synthetic delete failure"), {}]
        self.assertEqual(self.ack(replay, None, True), (False, False))
        self.assertFalse(OpsAuditLog.objects.filter(action=qa_ack.ACK).exists())
        raw["Attributes"]["ApproximateReceiveCount"] = "3"
        self.assertEqual(self.ack(self.scope(raw, data), None, True), (True, False))
        saved = OpsAuditLog.objects.get(action=qa_ack.ACK).payload
        self.assertEqual((saved["role"], saved["receive_count"], saved["log_id"]), ("parent", 3, log_id))
        self.assertTrue(saved["deduplicated"])
        self.assertEqual(OpsAuditLog.objects.filter(action=qa_ack.MOCK_SEND).count(), 1)

    def test_plan_role_must_match_the_request_and_missing_v1_role_allows_only_student(self):
        student = self.job()
        parent = self.job(role="parent")
        self.assertEqual(self.scope(*student).payload["role"], "student")
        for mode, student_notice, parent_notice, job in [
            ({}, "skip", "send", parent),
            ({"selected_role": "student"}, "skip", "send", parent),
            ({"selected_role": "parent"}, "send", "skip", student),
            ({"selected_role": None}, "send", "skip", student),
            ({}, "send", "send", student),
            ({"selected_role": "parent"}, "send", "send", parent),
        ]:
            with self.subTest(plan_role=mode, student_notice=student_notice, parent_notice=parent_notice):
                self.plan.payload.pop("selected_role", None)
                self.plan.payload.update(mode)
                self.plan.save(update_fields=["payload"])
                self.batch.student_notice, self.batch.parent_notice = student_notice, parent_notice
                self.batch.save(update_fields=["student_notice", "parent_notice"])
                with self.assertRaisesRegex(ValueError, "role"):
                    self.scope(*job)
        self.assertIsNone(self.scope(parent[0], {**parent[1], "target_id": "student:71"}))
        self.assertIsNone(self.scope(parent[0], {**parent[1], "event_type": "registration_approved_student"}))

    def test_scope_requires_exact_seal_worker_and_committed_owner_log(self):
        raw, data = self.job(72)
        scope, _, log_id = self.send(raw, data)
        with self.assertRaisesRegex(ValueError, "mock provider"):
            scope.observe_mock(MagicMock(), log_id)
        for key, value in (("SOLAPI_MOCK", "false"), ("MESSAGING_SQS_QUEUE_NAME", "other"),
                           ("DJANGO_SETTINGS_MODULE", "apps.api.config.settings.api"),
                           ("ACADEMY_DEVELOPMENT_RELEASE_ID", self.release.replace("123", "456"))):
            with self.subTest(key=key), patch.dict(os.environ, {key: value}), self.assertRaises(ValueError):
                self.scope(raw, data)
        with patch.object(sys.modules["__main__"], "__spec__", None), self.assertRaises(ValueError):
            self.scope(raw, data)
        with patch.dict(os.environ, {"ACADEMY_RUNTIME_ENV": "production"}), self.assertNumQueries(0):
            self.assertIsNone(self.scope(raw, data))
        self.assertIsNone(self.scope(raw, {**data, "target_id": "student:9999"}))
        self.assertIsNone(self.scope(raw, {**data, "business_idempotency_key": "unrelated"}))
        other_actor = EnrollmentNoticeRegistration.objects.create(
            tenant=self.tenant, lecture=self.lecture, actor_id=self.batch.actor_id + 1, request_id=self.request_id,
            student_ids=self.batch.student_ids, student_notice="send", parent_notice="skip")
        other_receipt = EnrollmentNoticeRecipient.objects.create(
            registration=other_actor, student_id=72, role="student", status="admitted",
            business_key="other-registration-key", outbox_id=73)
        self.assertIsNone(self.scope(raw, {**data, "business_idempotency_key": other_receipt.business_key,
                                            "domain_object_id": f"enrollment-notice:{other_receipt.pk}"}))
        self.assertIsNone(qa_ack.enrollment_ack_scope(self.cfg, raw, data, self.owner.pk, self.owner.pk))
        self.assertIsNone(qa_ack.enrollment_ack_scope(self.cfg, raw, data, self.tenant.pk, self.owner.pk))
        for wrong in ({"source_tenant_id": self.owner.pk}, {"tenant_id": self.tenant.pk},
                      {"sqs_message_id": str(uuid4())}, {"target_id": "student:9999"}, {"status": "sending"}):
            row = NotificationLog.objects.filter(pk=log_id)
            old = row.values(*wrong).get()
            row.update(**wrong)
            with self.subTest(wrong=wrong), self.assertRaises(NotificationLog.DoesNotExist):
                self.ack(scope, log_id)
            row.update(**old)
        with self.assertRaises(NotificationLog.DoesNotExist):
            self.ack(scope, log_id + 1)
        self.queue.delete_message.assert_not_called()
        Tenant.objects.filter(pk=self.owner.pk).update(code="other-development-tenant")
        with self.assertRaises(NotificationLog.DoesNotExist):
            self.ack(scope, log_id)
        self.queue.delete_message.assert_not_called()
        with self.assertRaises((KeyError, ValueError)):
            self.scope({"ReceiptHandle": str(uuid4())}, data)
        self.plan.payload = {**self.plan.payload, "setup_id": self.plan.pk}
        self.plan.save(update_fields=["payload"])
        with self.assertRaisesRegex(ValueError, "ownership mismatch"):
            self.scope(raw, data)

    def test_lost_database_response_to_committed_ack_never_redeletes_or_duplicates(self):
        scope, _, log_id = self.send(*self.job(72))
        get_or_create = OpsAuditLog.objects.get_or_create
        attempts = []

        def write(**event):
            result = get_or_create(**event)
            attempts.append(result[0].pk)
            if len(attempts) == 1:
                raise OperationalError("lost fixture response")
            return result

        with patch.object(OpsAuditLog.objects, "get_or_create", side_effect=write), patch.object(qa_ack.time, "sleep"):
            self.assertEqual(self.ack(scope, log_id), (True, False))
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0], attempts[1])
        self.assertEqual(OpsAuditLog.objects.filter(action=qa_ack.ACK).count(), 1)
        self.queue.delete_message.assert_called_once()

    @staticmethod
    def queue_adapter():
        adapter = SQSQueueClient.__new__(SQSQueueClient)
        adapter.region_name = "fixture"
        adapter.sqs = MagicMock()
        adapter.sqs.get_queue_url.return_value = {"QueueUrl": "fixture-messaging-queue"}
        return adapter

    def run_worker(self, adapter, messages, lock_outcomes=None):
        current_handle = None
        released = []

        def receive(**kwargs):
            nonlocal current_handle
            if messages:
                message = messages.pop(0)
                current_handle = message["ReceiptHandle"]
                return {"Messages": [message]}
            sqs_main._shutdown = True
            return {}

        adapter.sqs.receive_message.side_effect = receive
        with (
            patch.object(sqs_main, "_shutdown", False), patch.object(sqs_main.signal, "signal"),
            patch.object(sqs_main, "load_config", return_value=self.cfg),
            patch.object(sqs_main, "get_queue_client", return_value=adapter),
            patch.object(sqs_main, "acquire_job_lock", return_value=True, side_effect=lock_outcomes),
            patch.object(sqs_main, "release_job_lock", side_effect=lambda job: released.append((current_handle, job))),
            patch("apps.shared.utils.heartbeat.beat"), patch.object(sqs_main, "_record_progress"),
        ):
            self.assertEqual(sqs_main.main(), 0)
        return released

    def configure_route(self, trigger):
        self.enterContext(override_settings(OWNER_TENANT_ID=self.owner.pk, MESSAGING_TENANT_BINDING_KEY="fixture-only",
                                            SOLAPI_KAKAO_PF_ID="development-mock-pfid"))
        Tenant.objects.filter(pk__in=[self.tenant.pk, self.owner.pk]).update(messaging_is_active=True, messaging_base_price=0)
        template = MessageTemplate.objects.create(tenant=self.owner, name="QA", body="QA",
                                                  solapi_status="APPROVED", solapi_template_id="QA")
        AutoSendConfig.objects.update_or_create(tenant=self.owner, trigger=trigger, defaults={"template": template})

    def test_normal_sent_notice_keeps_lock_on_delete_failure_and_deduplicates_after_expiry(self):
        message_id = str(uuid4())
        trigger = "registration_approved_student"
        self.configure_route(trigger)
        student = create_student_account_fixture(tenant=self.tenant, password="fixture", student_data={
            "ps_number": "ACK001", "omr_code": "12345678", "name": "QA",
            "phone": "01098000001", "parent_phone": "01099000001",
        })
        adapter = self.queue_adapter()
        adapter.sqs.send_message.return_value = {"MessageId": message_id}
        with patch("apps.domains.messaging.sqs_queue.get_queue_client", return_value=adapter):
            self.assertTrue(MessagingSQSQueue(wake_messaging_workers=False).enqueue(
                tenant_id=self.owner.pk, source_tenant_id=self.tenant.pk, to=student.phone, text="QA",
                target_id=f"student:{student.pk}", target_type="account", event_type=trigger, template_id="QA"))
        body = adapter.sqs.send_message.call_args.kwargs["MessageBody"]
        handles = ["first-receive", "lock-still-held", "after-lock-expiry"]
        messages = [{"MessageId": message_id, "ReceiptHandle": handle, "Body": body,
                     "Attributes": {"ApproximateReceiveCount": str(n)}} for n, handle in enumerate(handles, 1)]
        adapter.sqs.delete_message.side_effect = [OSError("synthetic delete failure"), {}]
        with patch.object(MockSolapiMessageService, "send", autospec=True, side_effect=MockSolapiMessageService.send) as send:
            released = self.run_worker(adapter, messages, lock_outcomes=[True, False, True])
        send.assert_called_once()
        self.assertEqual(released, [(handles[2], "messaging:" + message_id)])
        deletes = [op.kwargs["ReceiptHandle"] for op in adapter.sqs.delete_message.call_args_list]
        self.assertEqual(deletes, [handles[0], handles[2]])
        log = NotificationLog.objects.get(tenant=self.owner, source_tenant=self.tenant, sqs_message_id=message_id)
        self.assertEqual((log.status, log.success, log.business_idempotency_key),
                         ("sent", True, json.loads(body)["business_idempotency_key"]))
        self.assertFalse(OpsAuditLog.objects.filter(actor_username=qa_ack.WORKER).exists())

    def test_student_only_signed_registration_through_consumer_mock_commit_and_dedup(self):
        self.verify_registration_flow("student", "parent")

    def test_parent_only_signed_registration_through_consumer_mock_commit_and_dedup(self):
        self.verify_registration_flow("parent", "student")

    def verify_registration_flow(self, selected, skipped):
        trigger = f"registration_approved_{selected}"
        self.configure_route(trigger)
        self.batch.delete()
        self.plan.delete()
        qa.mark_scope(self.tenant, self.release, selected)
        actor = get_user_model().objects.create_user(tenant=self.tenant, username="qa-notice-teacher", password="fixture")
        TenantMembership.ensure_active(tenant=self.tenant, user=actor, role="teacher")
        client = APIClient()
        client.force_authenticate(actor)
        headers = {"HTTP_HOST": "localhost", "HTTP_X_TENANT_CODE": self.tenant.code}
        accounts = {}
        for n in (1, 2):
            student = create_student_account_fixture(
                tenant=self.tenant, password="fixture", student_data={
                    "ps_number": f"ACK00{n}", "omr_code": f"{n:08}", "name": "QA",
                    "phone": f"01098{n:06}", "parent_phone": f"01099{n:06}",
                },
            )
            accounts[student.pk] = (student.user_id, student.phone) if selected == "student" else (
                student.parent.user_id, student.parent.phone)
        ids = list(accounts)
        self.plan.payload.update(student_ids=ids, redelivery_student_id=ids[0], actor_id=actor.pk, selected_role=selected)
        self.plan.save(force_insert=True)
        payload = {"lecture": self.lecture.pk, "students": ids, "request_id": self.request_id,
                   f"{selected}_notice": "send", f"{skipped}_notice": "skip"}
        registered = client.post("/api/v1/enrollments/register-with-notices/", payload, format="json", **headers)
        self.assertEqual(registered.status_code, 201)
        self.batch = EnrollmentNoticeRegistration.objects.get(tenant=self.tenant, actor_id=actor.pk, request_id=self.request_id)
        raw = []
        adapter = self.queue_adapter()

        def enqueue(QueueUrl, MessageBody, DelaySeconds):
            message_id = str(uuid4())
            raw.append({"MessageId": message_id, "ReceiptHandle": f"original-{len(raw)}", "Body": MessageBody,
                        "Attributes": {"ApproximateReceiveCount": "1"}})
            return {"MessageId": message_id}

        adapter.sqs.send_message.side_effect = enqueue
        with patch("apps.domains.messaging.sqs_queue.get_queue_client", return_value=adapter), patch.object(
                MessagingSQSQueue, "_wake_messaging_workers_after_enqueue"):
            self.assertEqual(process_registration_notices(registration=self.batch, limit=4), 0)
        self.assertEqual(len(raw), 2)
        self.assertCountEqual(list(self.batch.recipients.filter(role=selected).values_list("student_id", "account_id")),
                              [(student_id, account) for student_id, (account, phone) in accounts.items()])
        bodies = [json.loads(m["Body"]) for m in raw]
        self.assertCountEqual([(b["target_type"], b["target_id"], b["to"], b["event_type"]) for b in bodies],
                              [("account", f"{selected}:{s}", phone, trigger) for s, (_, phone) in accounts.items()])
        first, other = sorted(raw, key=lambda m: json.loads(m["Body"])["target_id"] != f"{selected}:{ids[0]}")
        retry = {**first, "ReceiptHandle": "second-receive", "Attributes": {"ApproximateReceiveCount": "2"}}

        def delete(QueueUrl, ReceiptHandle):
            self.assertTrue(connection.get_autocommit())
            msg = first if ReceiptHandle == "second-receive" else other
            self.assertTrue(NotificationLog.objects.filter(source_tenant=self.tenant, sqs_message_id=msg["MessageId"],
                                                            status="sent", success=True).exists())
            self.assertFalse(OpsAuditLog.objects.filter(action=qa_ack.ACK, payload__message_id=msg["MessageId"]).exists())
            return {}

        adapter.sqs.delete_message.side_effect = delete
        released = self.run_worker(adapter, [first, other, retry])
        self.assertEqual(released, [(m["ReceiptHandle"], "messaging:" + m["MessageId"]) for m in (first, other, retry)])
        adapter.sqs.change_message_visibility.assert_called_once_with(
            QueueUrl="fixture-messaging-queue", ReceiptHandle=first["ReceiptHandle"], VisibilityTimeout=3)
        self.assertEqual([op.kwargs["ReceiptHandle"] for op in adapter.sqs.delete_message.call_args_list],
                         [other["ReceiptHandle"], "second-receive"])
        readback = client.get("/api/v1/enrollments/registration-notices/", {"lecture": self.lecture.pk,
                              "request_id": self.request_id}, **headers)
        self.assertEqual(readback.status_code, 200)
        for saved in readback.data["students"]:
            self.assertEqual(saved[skipped], {"status": "skipped", "log_id": None})
            log = NotificationLog.objects.get(tenant=self.owner, source_tenant=self.tenant,
                    target_type="account", target_id=f"{selected}:{saved['id']}", notification_type=trigger)
            self.assertEqual(saved[selected], {"status": "accepted", "log_id": log.pk})
        self.assertFalse(self.batch.recipients.filter(role=skipped, outbox_id__isnull=False).exists())
        self.assertFalse(NotificationLog.objects.filter(source_tenant=self.tenant, target_id__startswith=f"{skipped}:").exists())
        evidence = qa.worker_evidence(self.plan, self.batch)
        self.assertIsNotNone(evidence)
        self.assertCountEqual(evidence["messages"], [first["MessageId"], other["MessageId"]])
        self.assertEqual((len(evidence["logs"]), len(evidence["outboxes"]), len(evidence["events"])), (2, 2, 5))
        foreign = Tenant.objects.create(code="qa-unrelated-source", name="QA")
        keep = NotificationLog.objects.create(
            tenant=self.owner, source_tenant=foreign, success=True, status="sent", message_mode="alimtalk",
            notification_type=trigger, target_type="account", target_id=f"{selected}:7000", sqs_message_id=str(uuid4()),
            business_idempotency_key=sha256(uuid4().bytes).hexdigest(),
        )
        redis = MemoryRedis()
        progress = {f"tenant:{self.owner.pk}:job:messaging:{mid}:progress" for mid in evidence["messages"]}
        redis.keys.update(progress)
        r2 = MagicMock()
        r2.list_objects_v2.return_value = {"Contents": [], "IsTruncated": False}
        procfs = MagicMock()
        procfs.iterdir.return_value = ()
        procfs.read_text.return_value = ""
        with (
            patch.object(qa, "check_mock"),  # Live release identity is checked by the fixed command on its host.
            patch.dict(os.environ, {"REDIS_HOST": "127.0.0.1", "REDIS_PORT": "6379"}),
            patch("libs.redis.client.get_redis_client", return_value=redis),
            patch("apps.core.services.development_qa_ai_cleanup.get_redis_client", return_value=redis),
            patch.object(scenario.Command, "_r2_storage_client", return_value=r2),
            patch.object(scenario, "Path", return_value=procfs),
        ):
            residue = qa.cleanup(self.tenant.code, self.capability)
            self.assertEqual(qa.cleanup(self.tenant.code, self.capability), residue)
        self.assertFalse(any(residue.values()), residue)
        self.assertTrue({"tenants", "users", "registration_batches", "notice_recipients", "notice_outboxes",
                         "owner_notice_logs", "worker_evidence", "messaging_redis"} <= residue.keys())
        self.assertEqual(set(redis.deleted), progress)
        r2.delete_objects.assert_not_called()
        self.assertEqual(list(NotificationLog.objects.filter(tenant=self.owner).values_list("pk", "source_tenant_id")),
                         [(keep.pk, foreign.pk)])
        self.assertFalse(OpsAuditLog.objects.filter(pk__in=evidence["events"]).exists())
        snapshot = OpsAuditLog.objects.get(action=qa.PREFIX + "cleanup", payload__setup_id=self.plan.payload["setup_id"])
        self.assertCountEqual(snapshot.payload["logs"], evidence["logs"])
        self.assertCountEqual(snapshot.payload["events"], evidence["events"])
        self.assertEqual(snapshot.payload["selected_role"], selected)

    def test_only_dev_messaging_requests_the_sqs_receive_count(self):
        client = SQSQueueClient.__new__(SQSQueueClient)
        client.sqs = MagicMock()
        with patch.object(client, "_get_queue_url", return_value="queue-url"):
            for queue in (qa_ack.QUEUE, "ordinary-queue"):
                client.receive_message(queue)
                fields = client.sqs.receive_message.call_args.kwargs
                self.assertEqual(fields.get("MessageSystemAttributeNames"), ["ApproximateReceiveCount"] if queue == qa_ack.QUEUE else None)
