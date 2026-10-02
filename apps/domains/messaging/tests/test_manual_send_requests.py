import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from django.urls import resolve, reverse
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.core.models.user import user_internal_username
from apps.domains.messaging.models import (
    ManualSendRequest, MessageTemplate, MessagingObserver, NotificationLog, ScheduledNotification,
)


User = get_user_model()
Student = apps.get_model("students", "Student")


class ManualSendFixture:
    def setUp(self):
        super().setUp()
        self.owner = Tenant.objects.create(
            code="trace-provider", name="Provider", is_active=True,
            messaging_is_active=True, messaging_sender="0212345678", credit_balance=Decimal("1000"),
        )
        self.tenant = Tenant.objects.create(
            code="trace-business", name="Business", is_active=True,
            messaging_is_active=True, messaging_base_price=Decimal("8"), credit_balance=Decimal("1000"),
        )
        settings = override_settings(
            OWNER_TENANT_ID=self.owner.pk, SOLAPI_SENDER="0212345678",
            SOLAPI_KAKAO_PF_ID="MOCK-COMMON-CHANNEL", MESSAGING_TENANT_BINDING_ENFORCED=True,
        )
        settings.enable()
        self.addCleanup(settings.disable)
        self.admin = self.make_user("owner", "trace-owner")
        self.student = self.make_student(self.tenant, "TRACE01")
        self.template = self.make_template(self.tenant)
        self.request_id = str(uuid4())
        self.enqueue_patcher = patch("apps.domains.messaging.services.enqueue_alimtalk", return_value=True)
        self.enqueue = self.enqueue_patcher.start()
        self.addCleanup(self.enqueue_patcher.stop)
        site = patch("apps.domains.messaging.services.get_tenant_site_url", return_value="https://unit.example.test")
        site.start()
        self.addCleanup(site.stop)

    def make_user(self, role, username, tenant=None, phone=""):
        tenant = tenant or self.tenant
        user = User.objects.create_user(
            username=username, password="unit-test-only", tenant=tenant, is_staff=True, phone=phone,
        )
        TenantMembership.ensure_active(tenant=tenant, user=user, role=role)
        return user

    def make_student(self, tenant, suffix):
        user = User.objects.create_user(
            username=user_internal_username(tenant, suffix), password="unit-test-only", tenant=tenant,
            phone="01011112222", name="안내학생",
        )
        student = Student.objects.create(
            tenant=tenant, user=user, ps_number=suffix, name="안내학생", phone="01011112222",
            parent_phone="01033334444", omr_code="11112222",
        )
        TenantMembership.ensure_active(tenant=tenant, user=user, role="student")
        return student

    def make_template(self, tenant):
        return MessageTemplate.objects.create(
            tenant=tenant, category="lecture", name="안내", body="최초 안내 #{학생이름}", is_system=False,
        )

    def payload(self, **changes):
        return {
            "client_request_id": self.request_id,
            "student_ids": [self.student.pk], "send_to": "student",
            "template_id": self.template.pk, "block_category": "attendance", **changes,
        }

    def post(self, data=None, *, user=None, tenant=None, execute_callbacks=True):
        request = APIRequestFactory().post(reverse("messaging-send"), data or self.payload(), format="json")
        force_authenticate(request, user=user or self.admin)
        request.tenant = tenant or self.tenant
        if execute_callbacks:
            with TestCase.captureOnCommitCallbacks(execute=True):
                return resolve(request.path).func(request)
        return resolve(request.path).func(request)

    def logs(self, *, user=None, tenant=None, request_id=None, **filters):
        request = APIRequestFactory().get(
            reverse("messaging-log"), {"request_id": request_id or self.request_id, **filters},
        )
        force_authenticate(request, user=user or self.admin)
        request.tenant = tenant or self.tenant
        return resolve(request.path).func(request)

    def stored_log(self, *, tenant=None, status="sent", success=True):
        return NotificationLog.objects.create(
            tenant=self.owner, source_tenant=tenant or self.tenant,
            origin_type="manual_send", origin_id=self.request_id, notification_type="manual_send",
            message_mode="alimtalk", status=status, success=success,
            recipient_summary="안내학생 0101****", message_body="비공개 저장 본문",
            provider_message_id="provider-mock-private-123456", target_type="student",
            target_id=str(self.student.pk), target_name="안내학생",
        )


class ManualSendRequestTests(ManualSendFixture, TestCase):
    def test_first_admission_lost_response_replay_and_waiting_trace(self):
        first = self.post()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.data["accepted_count"], 1)
        self.assertEqual(first.data["enqueued"], 0)  # Outer TestCase commit has not happened yet.
        self.assertEqual(first.data["request_id"], self.request_id)
        receipt = ManualSendRequest.objects.get()
        original_ids = list(receipt.outbox_ids)
        second = self.post()  # First response may have been lost; request identity survives.
        self.assertTrue(second.data["replayed"])
        self.assertEqual(second.data["request_id"], self.request_id)
        self.assertEqual(ManualSendRequest.objects.get().outbox_ids, original_ids)
        self.assertEqual(ScheduledNotification.objects.count(), 1)
        self.enqueue.assert_called_once()
        trace = self.logs().data["request_trace"]
        self.assertEqual((trace["accepted_count"], trace["enqueued"], trace["provider_accepted_count"]), (1, 1, 0))
        self.assertIsNone(trace["delivered_count"])
        self.assertEqual(self.logs().data["results"], [])

    def test_template_phone_and_observer_changes_never_rebuild_first_payload(self):
        observer = self.make_user("staff", "trace-observer", phone="01077778888")
        MessagingObserver.objects.create(tenant=self.tenant, user=observer)
        self.post()
        ids = list(ScheduledNotification.objects.values_list("pk", flat=True))
        first_calls = list(self.enqueue.call_args_list)
        self.assertEqual(len(first_calls), 2)
        original = next(call.kwargs for call in first_calls if call.kwargs["target_type"] == "student")
        self.assertEqual(original["to"], "01011112222")
        self.assertIn("최초 안내", original["text"])
        self.template.body = "변경된 문구"
        self.template.save(update_fields=["body"])
        self.student.phone = "01055556666"
        self.student.save(update_fields=["phone"])
        observer.phone = "01099998888"
        observer.save(update_fields=["phone"])
        with patch("apps.domains.messaging.views.send_views.resolve_student_message_recipients") as resolve:
            response = self.post()
        self.assertTrue(response.data["replayed"])
        resolve.assert_not_called()
        self.assertEqual(list(ScheduledNotification.objects.values_list("pk", flat=True)), ids)
        self.assertEqual(self.enqueue.call_args_list, first_calls)
        self.assertNotIn("text", ScheduledNotification.objects.get(pk=ids[0]).payload)

    def test_partial_student_parent_retry_reuses_completed_scope(self):
        self.post()
        with patch("apps.domains.messaging.alimtalk_content_builders.get_unified_for_manual_send", return_value=("attendance", "")):
            rejected = self.post(self.payload(send_to="parent"))
        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(ManualSendRequest.objects.count(), 1)
        parent = self.post(self.payload(send_to="parent"))
        self.assertEqual(parent.status_code, 200)
        self.assertEqual(ManualSendRequest.objects.count(), 2)
        self.assertTrue(self.post().data["replayed"])
        self.assertTrue(self.post(self.payload(send_to="parent")).data["replayed"])
        self.assertEqual(ScheduledNotification.objects.count(), 2)
        self.assertEqual(self.enqueue.call_count, 2)
        self.assertEqual({call.kwargs["target_type"] for call in self.enqueue.call_args_list}, {"student", "parent"})
        self.assertEqual(self.logs().data["request_trace"]["accepted_count"], 2)

    def test_changed_payload_or_actor_conflicts_without_second_admission(self):
        self.post()
        other = self.make_user("admin", "trace-other-admin")
        for data, user in [(self.payload(raw_body="다른 본문"), self.admin), (self.payload(send_to="parent"), other)]:
            with self.subTest(actor=user.pk, send_to=data["send_to"]):
                response = self.post(data, user=user)
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.data["code"], "manual_request_conflict")
        self.assertEqual(ManualSendRequest.objects.count(), 1)
        self.assertEqual(ScheduledNotification.objects.count(), 1)
        self.enqueue.assert_called_once()

    def test_new_explicit_request_can_send_again(self):
        self.post()
        response = self.post(self.payload(client_request_id=str(uuid4()), raw_body="새 요청"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["replayed"])
        self.assertEqual(ManualSendRequest.objects.count(), 2)
        self.assertEqual(self.enqueue.call_count, 2)

    def test_invalid_uuid_and_wrong_alias_leave_no_admission(self):
        for data in [self.payload(client_request_id="invalid"), self.payload(request_id=self.request_id)]:
            with self.subTest(data=data):
                self.assertEqual(self.post(data).status_code, 400)
        self.assertEqual(ManualSendRequest.objects.count(), 0)
        self.assertEqual(ScheduledNotification.objects.count(), 0)
        self.enqueue.assert_not_called()
        self.assertEqual(self.logs(request_id="invalid").status_code, 400)

    def test_no_phone_is_visible_failure_with_zero_side_effects(self):
        self.student.phone = ""
        self.student.save(update_fields=["phone"])
        self.student.user.phone = ""
        self.student.user.save(update_fields=["phone"])
        response = self.post()
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.data["accepted_count"], 0)
        self.assertEqual(ManualSendRequest.objects.count(), 0)
        self.assertEqual(ScheduledNotification.objects.count(), 0)
        self.enqueue.assert_not_called()

    def test_queue_outage_replay_keeps_one_durable_retry(self):
        self.enqueue.return_value = False
        first = self.post()
        second = self.post()
        self.assertEqual(first.data["accepted_count"], 1)
        self.assertTrue(second.data["replayed"])
        self.assertEqual(second.data["enqueued"], 0)
        self.assertEqual(second.data["scheduled"], 1)
        self.assertEqual(ScheduledNotification.objects.get().status, ScheduledNotification.Status.PENDING)
        self.enqueue.assert_called_once()

    def test_outer_rollback_removes_receipt_original_observer_and_queue_callback(self):
        observer = self.make_user("staff", "rollback-observer", phone="01077778888")
        MessagingObserver.objects.create(tenant=self.tenant, user=observer)
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaisesRegex(RuntimeError, "rollback"):
                with transaction.atomic():
                    response = self.post(execute_callbacks=False)
                    self.assertEqual(response.data["accepted_count"], 1)
                    self.assertEqual(ScheduledNotification.objects.count(), 2)
                    self.assertEqual(ManualSendRequest.objects.count(), 1)
                    self.enqueue.assert_not_called()
                    raise RuntimeError("rollback")
        self.assertEqual(ManualSendRequest.objects.count(), 0)
        self.assertEqual(ScheduledNotification.objects.count(), 0)
        self.enqueue.assert_not_called()

    def test_elapsed_schedule_replays_but_new_past_schedule_is_rejected(self):
        send_at = timezone.now() + timedelta(minutes=30)
        data = self.payload(scheduled_send_at=send_at.isoformat())
        self.assertEqual(self.post(data).status_code, 200)
        with patch("django.utils.timezone.now", return_value=send_at + timedelta(minutes=1)):
            replay = self.post(data)
            fresh = self.post({**data, "client_request_id": str(uuid4())})
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.data["replayed"])
        self.assertEqual(fresh.status_code, 400)
        self.assertEqual(ManualSendRequest.objects.count(), 1)
        self.assertEqual(ScheduledNotification.objects.count(), 1)
        self.enqueue.assert_not_called()

    def test_teacher_own_trace_reload_and_other_actor_are_masked(self):
        teacher = self.make_user("teacher", "trace-teacher")
        other = self.make_user("teacher", "trace-other-teacher")
        self.assertEqual(self.post(user=teacher).status_code, 200)
        self.stored_log()
        for response in [self.logs(user=teacher), self.logs(user=teacher, page=1)]:
            self.assertEqual(response.data["count"], 1)
            self.assertEqual(response.data["request_trace"]["provider_accepted_count"], 1)
            row = response.data["results"][0]
            self.assertEqual(row["body_visibility"], "restricted")
            self.assertEqual(row["message_body"], "")
            self.assertEqual(row["provider_message_id"], "")
            encoded = json.dumps(response.data, ensure_ascii=False, default=str)
            self.assertNotIn("비공개 저장 본문", encoded)
            self.assertNotIn("provider-mock-private-123456", encoded)
        self.assertEqual(self.logs(user=other).data, {"results": [], "count": 0, "request_trace": None})
        self.assertEqual(self.logs().data["count"], 1)  # Authorized admin can inspect the teacher request.

    def test_same_uuid_in_another_tenant_has_independent_receipt_and_logs(self):
        self.post()
        self.stored_log()
        other_tenant = Tenant.objects.create(code="trace-other", name="Other", is_active=True, messaging_is_active=True)
        other_admin = self.make_user("owner", "trace-foreign-owner", tenant=other_tenant)
        self.assertEqual(self.logs(user=other_admin, tenant=other_tenant).data["request_trace"], None)
        other_student = self.make_student(other_tenant, "TRACE02")
        other_template = self.make_template(other_tenant)
        response = self.post(self.payload(student_ids=[other_student.pk], template_id=other_template.pk), user=other_admin, tenant=other_tenant)
        self.assertEqual(response.status_code, 200)
        self.stored_log(tenant=other_tenant, status="failed", success=False)
        own = self.logs().data
        foreign = self.logs(user=other_admin, tenant=other_tenant).data
        self.assertEqual((own["count"], own["request_trace"]["provider_accepted_count"]), (1, 1))
        self.assertEqual((foreign["count"], foreign["request_trace"]["provider_failed_count"]), (1, 1))

    def test_request_summary_preserves_ambiguous_and_is_independent_of_row_filter(self):
        self.post()
        self.stored_log(status="ambiguous", success=False)
        response = self.logs(status="success")
        self.assertEqual(response.data["results"], [])
        self.assertEqual(response.data["request_trace"]["provider_ambiguous_count"], 1)
        self.assertEqual(response.data["request_trace"]["provider_accepted_count"], 0)
        self.assertIsNone(response.data["request_trace"]["delivered_count"])

    def run_worker_duplicate(self, *, ambiguous, different_message_id):
        from apps.worker.messaging_worker import sqs_main
        self.enqueue_patcher.stop()
        fake_queue = MagicMock()
        fake_queue.send_message.return_value = True
        def provider(*args, before_provider_call=None, **kwargs):
            self.assertTrue(before_provider_call())
            return {"status": "error", "reason": "network timeout"} if ambiguous else {"status": "ok", "group_id": "provider-mock-only"}
        with patch("apps.domains.messaging.sqs_queue.get_queue_client", return_value=fake_queue), patch(
            "apps.domains.messaging.policy.check_recipient_allowed", return_value=True,
        ), patch("academy.adapters.compute.ec2_control.ensure_messaging_worker_asg_min_capacity"):
            self.assertEqual(self.post().status_code, 200)
            self.assertTrue(self.post().data["replayed"])
        self.assertEqual(fake_queue.send_message.call_count, 1)
        message = fake_queue.send_message.call_args.kwargs["message"]
        self.assertTrue(message["occurrence_key"].startswith("dispatch:manual:"))
        raw = [
            {"Body": json.dumps(message), "ReceiptHandle": "receipt-first", "MessageId": "message-first"},
            {"Body": json.dumps(message), "ReceiptHandle": "receipt-second", "MessageId": "message-second" if different_message_id else "message-first"},
        ]
        def receive(**kwargs):
            if raw:
                return raw.pop(0)
            sqs_main._shutdown = True
            return None
        fake_queue.receive_message.side_effect = receive
        config = SimpleNamespace(
            MESSAGING_SQS_QUEUE_NAME="manual-worker-mock", SQS_WAIT_TIME_SECONDS=0, TEST_TENANT_ID=9999,
            OWNER_TENANT_ID=self.owner.pk, SOLAPI_SENDER="0212345678", SOLAPI_KAKAO_PF_ID="MOCK-COMMON-CHANNEL",
        )
        sqs_main._shutdown = False
        try:
            with patch.object(sqs_main, "load_config", return_value=config), patch.object(sqs_main, "get_queue_client", return_value=fake_queue), patch.object(
                sqs_main, "acquire_job_lock", return_value=True,
            ), patch.object(sqs_main, "release_job_lock"), patch.object(sqs_main, "_record_progress"), patch.object(
                sqs_main.signal, "signal",
            ), patch.object(sqs_main, "send_one_alimtalk", side_effect=provider) as send:
                self.assertEqual(sqs_main.main(), 0)
                self.assertEqual(send.call_count, 1)
        finally:
            sqs_main._shutdown = False
        log = NotificationLog.objects.get(origin_type="manual_send", origin_id=self.request_id)
        self.assertEqual(log.status, "ambiguous" if ambiguous else "sent")
        self.assertEqual(fake_queue.delete_message.call_count, 2)
        self.assertEqual(log.source_tenant_id, self.tenant.pk)
        self.assertEqual(log.amount_deducted, Decimal("8"))
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.credit_balance, Decimal("992"))

    def test_successful_same_sqs_message_duplicate_never_calls_provider_twice(self):
        self.run_worker_duplicate(ambiguous=False, different_message_id=False)

    def test_successful_different_sqs_message_duplicate_never_calls_provider_twice(self):
        self.run_worker_duplicate(ambiguous=False, different_message_id=True)

    def test_ambiguous_same_sqs_message_duplicate_keeps_credit_and_never_resends(self):
        self.run_worker_duplicate(ambiguous=True, different_message_id=False)

    def test_ambiguous_different_sqs_message_duplicate_keeps_credit_and_never_resends(self):
        self.run_worker_duplicate(ambiguous=True, different_message_id=True)


@skipUnless(connection.vendor == "postgresql", "requires real PostgreSQL row locking")
class ManualSendRequestPostgreSQLTests(ManualSendFixture, TransactionTestCase):
    def race(self, cases):
        barrier = threading.Barrier(2)
        def submit(case):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                data, user = case
                response = self.post(data, user=user)
                return response.status_code, response.data
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit, case) for case in cases]
            results = [future.result(timeout=30) for future in futures]
        self.assertEqual(ManualSendRequest.objects.count(), 1)
        self.assertEqual(ScheduledNotification.objects.count(), 1)
        self.enqueue.assert_called_once()
        return results

    def test_concurrent_identical_request_is_one_admission(self):
        results = self.race([(self.payload(), self.admin), (self.payload(), self.admin)])
        self.assertEqual([status for status, _ in results], [200, 200])
        self.assertEqual({data["request_id"] for _, data in results}, {self.request_id})
        self.assertEqual(sorted(data["replayed"] for _, data in results), [False, True])

    def test_concurrent_changed_payload_conflicts(self):
        results = self.race([(self.payload(), self.admin), (self.payload(raw_body="다른 작성 내용"), self.admin)])
        self.assertEqual(sorted(status for status, _ in results), [200, 409])

    def test_concurrent_different_actor_conflicts(self):
        other = self.make_user("admin", "trace-racing-admin")
        results = self.race([(self.payload(), self.admin), (self.payload(), other)])
        self.assertEqual(sorted(status for status, _ in results), [200, 409])

