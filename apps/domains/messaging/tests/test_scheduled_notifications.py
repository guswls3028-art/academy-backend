from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.core.models import Tenant
from apps.domains.messaging.models import ScheduledNotification
from apps.domains.messaging.scheduled import process_due_notifications


class ScheduledNotificationProcessingTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="msg-scheduled", name="Msg Scheduled", is_active=True)

    def test_enqueue_false_marks_due_notification_failed(self):
        notification = ScheduledNotification.objects.create(
            tenant=self.tenant,
            trigger="clinic_reminder",
            send_at=timezone.now(),
            payload={
                "tenant_id": self.tenant.id,
                "to": "01011112222",
                "text": "test",
                "message_mode": "alimtalk",
            },
        )

        with patch("apps.domains.messaging.services.enqueue_sms", return_value=False):
            stats = process_due_notifications(batch_size=10)

        self.assertEqual(stats, {"processed": 1, "sent": 0, "failed": 1})
        notification.refresh_from_db()
        self.assertEqual(notification.status, ScheduledNotification.Status.FAILED)
        self.assertIn("enqueue_sms returned false", notification.error_message)
