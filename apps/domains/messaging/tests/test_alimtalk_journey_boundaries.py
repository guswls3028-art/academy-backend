from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.domains.messaging.models import AutoSendConfig, MessageTemplate, ScheduledNotification, NotificationPreviewToken
from apps.domains.messaging.notification_dispatch import build_student_list_preview
from apps.domains.messaging.serializers import MessageTemplateSerializer
from apps.domains.messaging.tests import test_notification_preview_views as fixtures

Student = fixtures.Student


class AlimtalkJourneyBoundaryTests(TestCase):
    setUp = fixtures.NotificationPreviewViewValidationTests.setUp

    def _student(self, *, withdrawn):
        return Student.objects.create(
            tenant=self.tenant, user=self.admin, name="합성학생", ps_number="QA-WITHDRAWAL",
            omr_code="77112233", phone="01011112222", parent_phone="01033334444",
            deleted_at=timezone.now() if withdrawn else None,
        )

    def _withdrawal_config(self):
        template = MessageTemplate.objects.create(
            tenant=self.tenant, name="퇴원 안내", body="#{학생이름} 퇴원 안내",
        )
        AutoSendConfig.objects.create(
            tenant=self.tenant, trigger="withdrawal_complete", template=template,
            enabled=False, message_mode="alimtalk",
        )

    def test_withdrawal_preview_accepts_withdrawn_student_and_rejects_active_student(self):
        student = self._student(withdrawn=True)
        self._withdrawal_config()
        preview = build_student_list_preview(self.tenant, "withdrawal_complete", [student.id])
        self.assertEqual(preview["total_count"], 1)
        self.assertEqual(preview["recipients"][0]["student_id"], student.id)
        self.assertTrue(preview["recipients"][0]["full_message_body"])
        student.deleted_at = None
        student.save(update_fields=["deleted_at"])
        self.assertEqual(build_student_list_preview(
            self.tenant, "withdrawal_complete", [student.id],
        )["total_count"], 0)

    def test_creating_and_moving_default_templates_preserves_one_default_per_category(self):
        previous = MessageTemplate.objects.create(
            tenant=self.tenant, name="기존 기본", body="기존 문구", category="grades",
            is_user_default=True,
        )
        serializer = MessageTemplateSerializer(data={
            "name": "새 기본", "body": "새 문구", "category": "grades", "is_user_default": True,
        })
        serializer.is_valid(raise_exception=True)
        saved = serializer.save(tenant=self.tenant)
        previous.refresh_from_db()
        self.assertFalse(previous.is_user_default)
        other = MessageTemplate.objects.create(
            tenant=self.tenant, name="출석 기본", body="출석 문구", category="attendance",
            is_user_default=True,
        )
        serializer = MessageTemplateSerializer(saved, data={"category": "attendance"}, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        other.refresh_from_db()
        self.assertFalse(other.is_user_default)
        self.assertEqual(MessageTemplate.objects.filter(
            tenant=self.tenant, category="attendance", is_user_default=True,
        ).count(), 1)


class ConfirmationRecoveryTests(TestCase):
    setUp = fixtures.NotificationPreviewConfirmDurabilityTests.setUp
    _token = fixtures.NotificationPreviewConfirmDurabilityTests._token
    _confirm = fixtures.NotificationPreviewConfirmDurabilityTests._confirm

    @patch("apps.domains.messaging.policy.check_recipient_allowed", return_value=True)
    def test_acknowledgement_loss_recovers_original_receipt_without_redispatch(self, _allowed):
        token = self._token()
        with patch("apps.domains.messaging.scheduled.process_due_notifications", side_effect=RuntimeError("lost response")):
            with self.assertRaises(RuntimeError):
                self._confirm(token)
        with patch("apps.domains.messaging.scheduled.process_due_notifications") as dispatch:
            replay = self._confirm(token)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.data["accepted_count"], 1)
        self.assertEqual(replay.data["pending_count"], 1)
        self.assertEqual(ScheduledNotification.objects.count(), 1)
        dispatch.assert_not_called()
        token.refresh_from_db()
        self.assertEqual(str(token.batch_id), replay.data["batch_id"])
        self.assertNotIn("01012345678", str(token.payload))

    @patch("apps.domains.messaging.policy.check_recipient_allowed", return_value=False)
    def test_blocked_only_request_has_no_success_and_no_outbox(self, _allowed):
        token = self._token()
        response = self._confirm(token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["accepted_count"], 0)
        self.assertEqual(response.data["blocked_count"], 1)
        self.assertFalse(ScheduledNotification.objects.exists())

    @patch("apps.domains.messaging.policy.check_recipient_allowed", return_value=True)
    @patch("apps.domains.messaging.scheduled.process_due_notifications")
    def test_receipt_survives_unused_token_ttl_and_contains_no_delivery_payload(self, _dispatch, _allowed):
        from datetime import timedelta

        token = self._token()
        response = self._confirm(token)
        token.refresh_from_db()
        self.assertGreater(token.expires_at, timezone.now() + timedelta(hours=23))
        self.assertEqual(set(token.payload), {"redacted", "recipients", "notification_type", "send_to", "confirmation"})
        with patch("apps.domains.messaging.notification_dispatch.timezone.now", return_value=timezone.now() + timedelta(minutes=6)):
            retry = self._confirm(token)
        self.assertEqual(retry.data["batch_id"], response.data["batch_id"])
        _dispatch.assert_called_once()

    def test_another_tenant_cannot_read_or_consume_a_preview(self):
        from apps.core.models import Tenant
        from apps.domains.messaging.notification_dispatch import consume_preview_token_and_execute

        token = self._token()
        other = Tenant.objects.create(code="qa-other-confirm", name="합성 타 학원")
        result = consume_preview_token_and_execute(str(token.token), other, session_type="manual")
        self.assertEqual(result["status"], 400)
        token.refresh_from_db()
        self.assertIsNone(token.used_at)
        self.assertFalse(ScheduledNotification.objects.exists())

    def test_attendance_token_cannot_be_confirmed_by_manual_endpoint(self):
        token = self._token()
        token.session_type = "attendance"
        token.save(update_fields=["session_type"])
        with patch("apps.domains.messaging.scheduled.create_notification_outboxes") as create:
            response = self._confirm(token)
        self.assertEqual(response.status_code, 400)
        create.assert_not_called()
        token.refresh_from_db()
        self.assertIsNone(token.used_at)


class WithdrawalConfirmationTests(TestCase):
    setUp = AlimtalkJourneyBoundaryTests.setUp
    _student = AlimtalkJourneyBoundaryTests._student
    _withdrawal_config = AlimtalkJourneyBoundaryTests._withdrawal_config
    _post = fixtures.NotificationPreviewViewValidationTests._post

    def test_restoration_or_a_later_withdrawal_invalidates_the_old_preview(self):
        from datetime import timedelta

        student = self._student(withdrawn=True)
        self._withdrawal_config()
        response = self._post(fixtures.ManualNotificationPreviewView, "/preview/", {
            "trigger": "withdrawal_complete", "student_ids": [student.id], "send_to": "parent",
        })
        self.assertEqual(response.status_code, 200)
        token = NotificationPreviewToken.objects.get(token=response.data["preview_token"])
        withdrawn_at = student.deleted_at
        for changed_at in [None, withdrawn_at + timedelta(minutes=1)]:
            student.deleted_at = changed_at
            student.save(update_fields=["deleted_at"])
            with patch("apps.domains.messaging.scheduled.create_notification_outboxes") as create:
                response = self._post(fixtures.ManualNotificationConfirmView, "/confirm/", {
                    "preview_token": str(token.token),
                })
            self.assertEqual(response.status_code, 400)
            create.assert_not_called()
            token.refresh_from_db()
            self.assertIsNone(token.used_at)

    @patch("apps.domains.messaging.policy.check_recipient_allowed", return_value=True)
    @patch("apps.domains.messaging.services.enqueue_alimtalk", return_value=True)
    def test_withdrawal_preview_confirm_and_retry_keep_one_original_outbox(self, enqueue, _allowed):
        student = self._student(withdrawn=True)
        self._withdrawal_config()
        preview = self._post(fixtures.ManualNotificationPreviewView, "/preview/", {
            "trigger": "withdrawal_complete", "student_ids": [student.id], "send_to": "parent",
        })
        self.assertEqual(preview.status_code, 200)
        token = preview.data["preview_token"]
        self.assertIsNotNone(token)
        result = self._post(fixtures.ManualNotificationConfirmView, "/confirm/", {"preview_token": token})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data["accepted_count"], 1)
        retry = self._post(fixtures.ManualNotificationConfirmView, "/confirm/", {"preview_token": token})
        self.assertEqual(retry.data["batch_id"], result.data["batch_id"])
        self.assertEqual(ScheduledNotification.objects.count(), 1)
        enqueue.assert_called_once()
