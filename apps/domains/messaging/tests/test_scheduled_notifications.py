from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.messaging.models import NotificationLog, ScheduledNotification
from apps.domains.messaging.scheduled import (
    DISPATCH_CLAIM_TIMEOUT,
    MAX_ENQUEUE_ATTEMPTS,
    create_notification_outboxes,
    process_due_notifications,
)
from apps.domains.messaging.selectors import get_hourly_notification_usage
from apps.domains.messaging.views.scheduled_views import (
    ScheduledNotificationCancelView,
    ScheduledNotificationListView,
)
from apps.domains.messaging.services.registration_service import (
    _dispatch_registration_durably,
)


User = get_user_model()


class ScheduledNotificationProcessingTests(TransactionTestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="msg-scheduled", name="Msg Scheduled", is_active=True)

    def _notification(self, **overrides):
        values = {
            "tenant": self.tenant,
            "trigger": "clinic_reminder",
            "send_at": timezone.now(),
            "payload": {
                "tenant_id": self.tenant.id,
                "to": "01011112222",
                "text": "test",
                "message_mode": "alimtalk",
            },
        }
        values.update(overrides)
        return ScheduledNotification.objects.create(**values)

    def test_enqueue_false_schedules_exponential_retry(self):
        notification = self._notification()

        with patch("apps.domains.messaging.services.enqueue_sms", return_value=False):
            stats = process_due_notifications(batch_size=10)

        self.assertEqual(
            stats,
            {
                "processed": 1,
                "sent": 0,
                "retried": 1,
                "failed": 0,
                "deferred": 0,
            },
        )
        notification.refresh_from_db()
        self.assertEqual(notification.status, ScheduledNotification.Status.PENDING)
        self.assertEqual(notification.attempt_count, 1)
        self.assertIsNotNone(notification.next_attempt_at)
        self.assertGreater(notification.next_attempt_at, notification.last_attempt_at)
        self.assertIn("enqueue_sms returned false", notification.error_message)

    def test_retry_reuses_stable_occurrence_key_then_marks_queue_acceptance(self):
        notification = self._notification()

        with patch(
            "apps.domains.messaging.services.enqueue_sms",
            side_effect=[False, True],
        ) as enqueue_sms:
            first_stats = process_due_notifications(batch_size=10)
            notification.refresh_from_db()
            first_occurrence = notification.payload["occurrence_key"]
            ScheduledNotification.objects.filter(id=notification.id).update(
                next_attempt_at=timezone.now() - timedelta(seconds=1)
            )
            second_stats = process_due_notifications(batch_size=10)

        self.assertEqual(first_stats["retried"], 1)
        self.assertEqual(second_stats["sent"], 1)
        self.assertEqual(enqueue_sms.call_count, 2)
        self.assertEqual(
            enqueue_sms.call_args_list[0].kwargs["occurrence_key"],
            enqueue_sms.call_args_list[1].kwargs["occurrence_key"],
        )
        self.assertEqual(first_occurrence, enqueue_sms.call_args.kwargs["occurrence_key"])
        notification.refresh_from_db()
        self.assertEqual(notification.status, ScheduledNotification.Status.SENT)
        self.assertEqual(notification.attempt_count, 2)
        self.assertIsNotNone(notification.sent_at)
        self.assertEqual(notification.payload["redacted"], True)
        self.assertNotIn("01011112222", str(notification.payload))
        self.assertNotIn("test", str(notification.payload))

    def test_stale_dispatching_claim_is_recovered_with_same_dispatch_key(self):
        notification = self._notification(
            status=ScheduledNotification.Status.DISPATCHING,
            attempt_count=1,
            last_attempt_at=timezone.now() - DISPATCH_CLAIM_TIMEOUT - timedelta(seconds=1),
        )

        with patch("apps.domains.messaging.services.enqueue_sms", return_value=True) as enqueue_sms:
            stats = process_due_notifications(batch_size=10)

        self.assertEqual(stats["sent"], 1)
        self.assertEqual(
            enqueue_sms.call_args.kwargs["occurrence_key"],
            f"dispatch:{notification.dispatch_key.hex}",
        )
        notification.refresh_from_db()
        self.assertEqual(notification.attempt_count, 2)

    def test_old_binary_null_dispatch_identity_is_normalized_before_enqueue(self):
        notification = self._notification(
            dispatch_key=None,
            business_idempotency_key="",
        )

        with patch("apps.domains.messaging.services.enqueue_sms", return_value=True) as enqueue_sms:
            stats = process_due_notifications(batch_size=1)

        self.assertEqual(stats["sent"], 1)
        notification.refresh_from_db()
        self.assertIsNotNone(notification.dispatch_key)
        self.assertTrue(notification.business_idempotency_key)
        self.assertEqual(
            enqueue_sms.call_args.kwargs["occurrence_key"],
            f"dispatch:{notification.dispatch_key.hex}",
        )

    def test_invalid_payload_is_terminal_without_sqs_call(self):
        notification = self._notification(
            payload={"tenant_id": self.tenant.id, "text": "test"},
        )

        with patch("apps.domains.messaging.services.enqueue_sms") as enqueue_sms:
            stats = process_due_notifications(batch_size=10)

        self.assertEqual(stats["failed"], 1)
        enqueue_sms.assert_not_called()
        notification.refresh_from_db()
        self.assertEqual(notification.status, ScheduledNotification.Status.FAILED)
        self.assertEqual(notification.error_message, "invalid_payload_missing_recipient")

    def test_non_object_payload_is_terminal_and_preserves_forensic_value(self):
        original_payload = ["legacy", "01011112222", "temporary-secret"]
        notification = self._notification(payload=original_payload)
        original_dispatch_key = notification.dispatch_key

        with patch("apps.domains.messaging.services.enqueue_sms") as enqueue_sms:
            stats = process_due_notifications(batch_size=10)

        self.assertEqual(stats["failed"], 1)
        enqueue_sms.assert_not_called()
        notification.refresh_from_db()
        self.assertEqual(notification.status, ScheduledNotification.Status.FAILED)
        self.assertEqual(notification.error_message, "invalid_payload_not_object")
        self.assertEqual(notification.payload, original_payload)
        self.assertEqual(notification.dispatch_key, original_dispatch_key)

    def test_sensitive_payload_is_redacted_after_queue_acceptance(self):
        notification = self._notification(
            trigger="registration_approved_student",
            payload={
                "tenant_id": self.tenant.id,
                "to": "01011112222",
                "text": "임시 비밀번호: secret-1234",
                "message_mode": "alimtalk",
                "event_type": "registration_approved_student",
                "target_type": "account",
                "target_id": "student:7",
                "alimtalk_replacements": ["secret-1234"],
            },
        )

        with patch(
            "apps.domains.messaging.services.enqueue_sms",
            return_value=True,
        ) as enqueue_sms:
            stats = process_due_notifications(batch_size=10)

        self.assertEqual(stats["sent"], 1)
        self.assertEqual(enqueue_sms.call_args.kwargs["to"], "01011112222")
        self.assertIn("secret-1234", enqueue_sms.call_args.kwargs["text"])
        notification.refresh_from_db()
        self.assertEqual(notification.payload["redacted"], True)
        self.assertEqual(notification.payload["target_id"], "student:7")
        self.assertNotIn("to", notification.payload)
        self.assertNotIn("text", notification.payload)
        self.assertNotIn("alimtalk_replacements", notification.payload)
        self.assertNotIn("01011112222", str(notification.payload))
        self.assertNotIn("secret-1234", str(notification.payload))

    def test_sensitive_retry_retains_payload_until_terminal_failure(self):
        notification = self._notification(
            trigger="password_reset_student",
            payload={
                "tenant_id": self.tenant.id,
                "to": "01011112222",
                "text": "temporary-secret",
                "message_mode": "alimtalk",
                "event_type": "password_reset_student",
            },
        )

        with patch("apps.domains.messaging.services.enqueue_sms", return_value=False):
            first_stats = process_due_notifications(batch_size=10)

        self.assertEqual(first_stats["retried"], 1)
        notification.refresh_from_db()
        self.assertEqual(notification.payload["to"], "01011112222")
        self.assertEqual(notification.payload["text"], "temporary-secret")

        ScheduledNotification.objects.filter(id=notification.id).update(
            attempt_count=MAX_ENQUEUE_ATTEMPTS - 1,
            next_attempt_at=timezone.now() - timedelta(seconds=1),
        )
        with patch("apps.domains.messaging.services.enqueue_sms", return_value=False):
            final_stats = process_due_notifications(batch_size=10)

        self.assertEqual(final_stats["failed"], 1)
        notification.refresh_from_db()
        self.assertEqual(notification.payload["redacted"], True)
        self.assertNotIn("01011112222", str(notification.payload))
        self.assertNotIn("temporary-secret", str(notification.payload))

    def test_retry_budget_exhaustion_is_terminal(self):
        notification = self._notification(attempt_count=MAX_ENQUEUE_ATTEMPTS - 1)

        with patch("apps.domains.messaging.services.enqueue_sms", return_value=False):
            stats = process_due_notifications(batch_size=10)

        self.assertEqual(stats["failed"], 1)
        notification.refresh_from_db()
        self.assertEqual(notification.status, ScheduledNotification.Status.FAILED)
        self.assertEqual(notification.attempt_count, MAX_ENQUEUE_ATTEMPTS)
        self.assertIn("enqueue_attempts_exhausted", notification.error_message)

    def test_existing_worker_log_reconciles_outbox_before_retry_exhaustion(self):
        notification = create_notification_outboxes(
            tenant_id=self.tenant.id,
            notifications=[
                {
                    "trigger": "sqs-response-lost",
                    "send_at": timezone.now(),
                    "payload": {
                        "tenant_id": self.tenant.id,
                        "to": "01011112222",
                        "text": "already consumed",
                        "message_mode": "alimtalk",
                    },
                }
            ],
        )[0]
        ScheduledNotification.objects.filter(pk=notification.pk).update(
            attempt_count=MAX_ENQUEUE_ATTEMPTS,
        )
        NotificationLog.objects.create(
            tenant=self.tenant,
            success=False,
            status="processing",
            message_mode="alimtalk",
            business_idempotency_key=notification.business_idempotency_key,
        )

        with patch("apps.domains.messaging.services.enqueue_sms") as enqueue_sms:
            stats = process_due_notifications(batch_size=1)

        enqueue_sms.assert_not_called()
        self.assertEqual(stats["sent"], 1)
        self.assertEqual(stats["failed"], 0)
        notification.refresh_from_db()
        self.assertEqual(notification.status, ScheduledNotification.Status.SENT)

    def test_sqs_call_runs_outside_database_transaction(self):
        from django.db import connection

        notification = self._notification()

        def assert_outside_transaction(**kwargs):
            self.assertFalse(connection.in_atomic_block)
            return True

        with patch(
            "apps.domains.messaging.services.enqueue_sms",
            side_effect=assert_outside_transaction,
        ):
            stats = process_due_notifications(batch_size=10)

        self.assertEqual(stats["sent"], 1)
        notification.refresh_from_db()
        self.assertEqual(notification.status, ScheduledNotification.Status.SENT)

    def test_future_reservations_do_not_consume_current_quota_and_are_throttled_when_due(self):
        future = timezone.now() + timedelta(days=3)
        notifications = create_notification_outboxes(
            tenant_id=self.tenant.id,
            notifications=[
                {
                    "trigger": "future_quota",
                    "send_at": future,
                    "payload": {
                        "tenant_id": self.tenant.id,
                        "to": f"0101111222{index}",
                        "text": "future",
                        "message_mode": "alimtalk",
                    },
                }
                for index in range(2)
            ],
        )
        self.assertEqual(get_hourly_notification_usage(self.tenant), 0)
        ScheduledNotification.objects.filter(
            id__in=[notification.id for notification in notifications]
        ).update(send_at=timezone.now())

        with (
            patch("apps.domains.messaging.scheduled.HOURLY_SEND_LIMIT", 1),
            patch("apps.domains.messaging.services.enqueue_sms", return_value=True),
        ):
            stats = process_due_notifications(batch_size=2)

        self.assertEqual(stats["sent"], 1)
        self.assertEqual(stats["deferred"], 1)
        self.assertEqual(
            ScheduledNotification.objects.filter(
                id__in=[notification.id for notification in notifications],
                status=ScheduledNotification.Status.PENDING,
                error_message="hourly_dispatch_quota_deferred",
            ).count(),
            1,
        )

    def test_quota_deduplicates_log_created_from_same_outbox_business_key(self):
        outbox = create_notification_outboxes(
            tenant_id=self.tenant.id,
            notifications=[
                {
                    "trigger": "quota-overlap",
                    "send_at": timezone.now(),
                    "payload": {
                        "tenant_id": self.tenant.id,
                        "to": "01011112222",
                        "text": "overlap",
                        "message_mode": "alimtalk",
                        "event_type": "quota-overlap",
                    },
                }
            ],
        )[0]
        ScheduledNotification.objects.filter(pk=outbox.pk).update(
            last_attempt_at=timezone.now(),
        )
        NotificationLog.objects.create(
            tenant=self.tenant,
            success=True,
            status="sent",
            message_mode="alimtalk",
            business_idempotency_key=outbox.business_idempotency_key,
        )

        self.assertEqual(get_hourly_notification_usage(self.tenant), 1)

    def test_quota_adds_disjoint_legacy_logs_including_blank_keys(self):
        outbox = create_notification_outboxes(
            tenant_id=self.tenant.id,
            notifications=[
                {
                    "trigger": "quota-disjoint",
                    "send_at": timezone.now(),
                    "payload": {
                        "tenant_id": self.tenant.id,
                        "to": "01011112222",
                        "text": "outbox",
                        "message_mode": "alimtalk",
                    },
                }
            ],
        )[0]
        ScheduledNotification.objects.filter(pk=outbox.pk).update(
            last_attempt_at=timezone.now(),
        )
        NotificationLog.objects.create(
            tenant=self.tenant,
            success=False,
            status="failed",
            message_mode="alimtalk",
            business_idempotency_key="",
        )

        self.assertEqual(get_hourly_notification_usage(self.tenant), 2)

    def test_outer_business_rollback_discards_outbox_and_sqs_callback(self):
        with patch("apps.domains.messaging.services.enqueue_sms") as enqueue_sms:
            with self.assertRaisesRegex(RuntimeError, "rollback business write"):
                with transaction.atomic():
                    _dispatch_registration_durably(
                        business_tenant_id=self.tenant.id,
                        trigger="registration_approved_student",
                        tenant_id=self.tenant.id,
                        to="01011112222",
                        text="가입 승인",
                        message_mode="alimtalk",
                        template_id="KA01TP_REG",
                        event_type="registration_approved_student",
                    )
                    raise RuntimeError("rollback business write")

        enqueue_sms.assert_not_called()
        self.assertFalse(ScheduledNotification.objects.exists())


class ScheduledNotificationViewTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(code="msg-scheduled-view", name="Msg Scheduled View", is_active=True)
        self.user = User.objects.create_user(
            username="msg-scheduled-owner",
            password="test1234",
            tenant=self.tenant,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="owner")

    def _request(self, method: str, path: str, data=None):
        request = getattr(self.factory, method)(path, data=data or {}, format="json")
        force_authenticate(request, user=self.user)
        request.user = self.user
        request.tenant = self.tenant
        return request

    def test_list_rejects_malformed_pagination(self):
        request = self._request(
            "get",
            "/api/v1/messaging/scheduled/?page=x&page_size=nope",
        )

        response = ScheduledNotificationListView.as_view()(request)

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("page", response.data["detail"])

    def test_list_pending_scheduled_notifications_masks_recipient(self):
        ScheduledNotification.objects.create(
            tenant=self.tenant,
            trigger="manual_send",
            send_at=timezone.now() + timedelta(minutes=30),
            payload={
                "tenant_id": self.tenant.id,
                "to": "01011112222",
                "text": "예약 본문",
                "message_mode": "alimtalk",
                "target_type": "parent",
                "target_id": 1,
                "target_name": "홍길동",
            },
        )

        response = ScheduledNotificationListView.as_view()(
            self._request("get", "/api/v1/messaging/scheduled/?status=pending")
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        item = response.data["results"][0]
        self.assertEqual(item["recipient_summary"], "홍길동 / 010****2222")
        self.assertEqual(item["message_preview"], "예약 본문")

    def test_list_redacts_legacy_parent_phone_target_and_sensitive_body(self):
        ScheduledNotification.objects.create(
            tenant=self.tenant,
            trigger="registration_approved_parent",
            send_at=timezone.now() + timedelta(minutes=30),
            payload={
                "tenant_id": self.tenant.id,
                "to": "01011112222",
                "text": "임시 비밀번호: secret-1234",
                "message_mode": "alimtalk",
                "target_type": "account",
                "target_id": "parent:123:01031217466",
            },
        )

        response = ScheduledNotificationListView.as_view()(
            self._request("get", "/api/v1/messaging/scheduled/?status=pending")
        )

        self.assertEqual(response.status_code, 200)
        item = response.data["results"][0]
        self.assertEqual(item["target_id"], "parent:123")
        self.assertNotIn("01031217466", str(item))
        self.assertNotIn("secret-1234", str(item))

    def test_cancel_pending_scheduled_notification(self):
        notification = ScheduledNotification.objects.create(
            tenant=self.tenant,
            trigger="manual_send",
            send_at=timezone.now() + timedelta(minutes=30),
            payload={"tenant_id": self.tenant.id, "to": "01011112222", "text": "예약 본문"},
        )

        response = ScheduledNotificationCancelView.as_view()(
            self._request("post", f"/api/v1/messaging/scheduled/{notification.id}/cancel/"),
            pk=notification.id,
        )

        self.assertEqual(response.status_code, 200)
        notification.refresh_from_db()
        self.assertEqual(notification.status, ScheduledNotification.Status.CANCELLED)
        self.assertEqual(notification.payload["redacted"], True)
        self.assertNotIn("01011112222", str(notification.payload))
        self.assertNotIn("예약 본문", str(notification.payload))

    def test_cancel_loses_compare_and_swap_after_dispatch_claim(self):
        notification = ScheduledNotification.objects.create(
            tenant=self.tenant,
            trigger="manual_send",
            send_at=timezone.now(),
            status=ScheduledNotification.Status.DISPATCHING,
            payload={"tenant_id": self.tenant.id, "to": "01011112222", "text": "예약 본문"},
        )

        response = ScheduledNotificationCancelView.as_view()(
            self._request("post", f"/api/v1/messaging/scheduled/{notification.id}/cancel/"),
            pk=notification.id,
        )

        self.assertEqual(response.status_code, 409)
        notification.refresh_from_db()
        self.assertEqual(notification.status, ScheduledNotification.Status.DISPATCHING)

    def test_registration_enqueue_failure_remains_in_durable_outbox(self):
        with patch("apps.domains.messaging.services.enqueue_sms", return_value=False):
            with self.captureOnCommitCallbacks(execute=True):
                accepted = _dispatch_registration_durably(
                    business_tenant_id=self.tenant.id,
                    trigger="registration_approved_student",
                    tenant_id=self.tenant.id,
                    to="01011112222",
                    text="가입 승인",
                    message_mode="alimtalk",
                    template_id="KA01TP_REG",
                    event_type="registration_approved_student",
                    target_type="account",
                    target_id="student:1",
                    source_tenant_id=self.tenant.id,
                )

        self.assertTrue(accepted)
        notification = ScheduledNotification.objects.get(tenant=self.tenant)
        self.assertEqual(notification.status, ScheduledNotification.Status.PENDING)
        self.assertEqual(notification.attempt_count, 1)
