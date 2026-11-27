"""Synthetic receipts for the dedicated development messaging consumer."""
import os
import sys
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.db import OperationalError, connection
from django.test import TransactionTestCase, override_settings

from academy.adapters.db.django.repositories_messaging import (
    claim_notification_slot, finalize_notification, mark_notification_sending,
)
from apps.core.models import OpsAuditLog, Tenant
from apps.core.services.development_qa_ownership import WRONG_NOTE_SETUP_SCHEMA
from apps.core.services.enrollment_notice_development_scenario import worker_evidence
from apps.domains.enrollment.models import EnrollmentNoticeRecipient, EnrollmentNoticeRegistration
from apps.domains.lectures.test_support import create_lecture_fixture
from apps.domains.messaging.models import AutoSendConfig, MessageTemplate, NotificationLog
from apps.domains.messaging.sqs_queue import MessagingSQSQueue
from apps.domains.students.test_support import create_student_account_fixture
from apps.support.enrollment.notice_dispatch import process_registration_notices
from apps.support.enrollment.registration_notices import register_with_notices
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
        setup = OpsAuditLog.objects.create(
            action="development.wrong-note.setup", target_tenant=self.tenant, actor_username="wrong-note-development-runner",
            payload={"schema": WRONG_NOTE_SETUP_SCHEMA, "tenant_code": self.tenant.code, "tenant_id": self.tenant.pk,
                     "owner_sha256": "b" * 64},
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

    def job(self, student_id=71):
        recipient = EnrollmentNoticeRecipient.objects.create(registration=self.batch, role="student",
            student_id=student_id, status="admitted", outbox_id=student_id, business_key=str(uuid4()))
        data = {"domain_object_id": f"enrollment-notice:{recipient.pk}", "event_type": "registration_approved_student",
                "target_type": "account", "target_id": f"student:{student_id}", "business_idempotency_key": recipient.business_key}
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
        result = sqs_main.send_one_alimtalk(
            self.cfg, "01098000001", "0212345678", "development-mock-pfid", "QA",
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

    def run_worker(self, messages):
        def receive(**kwargs):
            if messages:
                return messages.pop(0)
            sqs_main._shutdown = True
            return None

        self.queue.receive_message.side_effect = receive
        with patch.object(sqs_main, "_shutdown", False), patch.object(sqs_main.signal, "signal"), patch.object(
            sqs_main, "load_config", return_value=self.cfg
        ), patch.object(sqs_main, "get_queue_client", return_value=self.queue), patch.object(
            sqs_main, "acquire_job_lock", return_value=True
        ), patch.object(sqs_main, "release_job_lock") as unlock, patch("apps.shared.utils.heartbeat.beat"), patch.object(
            sqs_main, "_record_progress"
        ):
            self.assertEqual(sqs_main.main(), 0)
        return unlock

    def test_normal_worker_loop_keeps_lock_on_delete_failure(self):
        messages = [{"MessageId": str(uuid4()), "ReceiptHandle": "fixture", "Body": "invalid-json"} for _ in range(2)]
        second_id = messages[1]["MessageId"]
        self.queue.delete_message.side_effect = [False, True]
        unlock = self.run_worker(messages)
        unlock.assert_called_once_with("messaging:" + second_id)
        self.assertEqual(self.queue.delete_message.call_count, 2)

    @override_settings(MESSAGING_TENANT_BINDING_KEY="fixture-only")
    def test_signed_registration_envelope_through_consumer_mock_commit_and_dedup(self):
        self.enterContext(override_settings(OWNER_TENANT_ID=self.owner.pk))
        self.batch.delete()
        ids = []
        for n in (1, 2):
            student = create_student_account_fixture(
                tenant=self.tenant, password="fixture", student_data={
                    "ps_number": f"ACK00{n}", "omr_code": f"{n:08}", "name": "QA",
                    "phone": f"01098{n:06}", "parent_phone": f"01099{n:06}",
                },
            )
            ids.append(student.pk)
        self.plan.payload.update(student_ids=ids, redelivery_student_id=ids[0])
        self.plan.save(update_fields=["payload"])
        Tenant.objects.filter(pk__in=[self.tenant.pk, self.owner.pk]).update(messaging_is_active=True)
        template = MessageTemplate.objects.create(tenant=self.owner, name="QA", body="QA",
                                                  solapi_status="APPROVED", solapi_template_id="QA")
        AutoSendConfig.objects.update_or_create(tenant=self.owner, trigger="registration_approved_student",
                                                defaults={"template": template})
        self.batch = register_with_notices(tenant=self.tenant, actor_id=81, lecture=self.lecture.pk, students=ids,
                                           request_id=self.request_id, student_notice="send", parent_notice="skip")
        with patch("apps.domains.messaging.sqs_queue.get_queue_client", return_value=self.queue), patch.object(
                MessagingSQSQueue, "_wake_messaging_workers_after_enqueue"):
            self.assertEqual(process_registration_notices(registration=self.batch, limit=4), 0)
        self.assertEqual(self.queue.send_message.call_count, 2)
        raw = [{"MessageId": str(uuid4()), "ReceiptHandle": f"original-{i}", "Body": call.kwargs["message"],
                "Attributes": {"ApproximateReceiveCount": "1"}} for i, call in enumerate(self.queue.send_message.call_args_list)]
        first, other = sorted(raw, key=lambda x: x["Body"]["target_id"] != f"student:{ids[0]}")
        retry = {**first, "ReceiptHandle": "second-receive", "Attributes": {"ApproximateReceiveCount": "2"}}

        def delete(queue_name, receipt_handle):
            self.assertTrue(connection.get_autocommit())
            msg = first if receipt_handle == "second-receive" else other
            self.assertTrue(NotificationLog.objects.filter(source_tenant=self.tenant, sqs_message_id=msg["MessageId"],
                                                            status="sent", success=True).exists())
            self.assertFalse(OpsAuditLog.objects.filter(action=qa_ack.ACK, payload__message_id=msg["MessageId"]).exists())
            return True

        self.queue.delete_message.side_effect = delete
        with patch.object(sqs_main, "_resolve_tenant_delivery_context", return_value={
                "billing_info": {"base_price": "0"}, "billing_tenant_id": self.tenant.pk,
                "channel": {"pf_id": "development-mock-pfid"}, "channel_source": "common_owner", "template_id": "QA",
        }):
            unlock = self.run_worker([first, other, retry])
        self.assertEqual(unlock.call_count, 3)
        self.queue.change_message_visibility.assert_called_once_with(
            queue_name=qa_ack.QUEUE, receipt_handle=first["ReceiptHandle"], visibility_timeout=3)
        self.assertEqual([op.kwargs["receipt_handle"] for op in self.queue.delete_message.call_args_list],
                         [other["ReceiptHandle"], "second-receive"])
        evidence = worker_evidence(self.plan, self.batch)
        self.assertIsNotNone(evidence)
        self.assertCountEqual(evidence["messages"], [first["MessageId"], other["MessageId"]])
        self.assertEqual((len(evidence["logs"]), len(evidence["outboxes"]), len(evidence["events"])), (2, 2, 5))

    def test_only_dev_messaging_requests_the_sqs_receive_count(self):
        client = SQSQueueClient.__new__(SQSQueueClient)
        client.sqs = MagicMock()
        with patch.object(client, "_get_queue_url", return_value="queue-url"):
            for queue in (qa_ack.QUEUE, "ordinary-queue"):
                client.receive_message(queue)
                fields = client.sqs.receive_message.call_args.kwargs
                self.assertEqual(fields.get("MessageSystemAttributeNames"), ["ApproximateReceiveCount"] if queue == qa_ack.QUEUE else None)
