from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.messaging.models import ScheduledNotification
from apps.domains.messaging.scheduled import process_due_notifications
from apps.domains.messaging.views.scheduled_views import (
    ScheduledNotificationCancelView,
    ScheduledNotificationListView,
)


User = get_user_model()


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
