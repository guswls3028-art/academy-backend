from datetime import timedelta
from io import StringIO
from uuid import uuid4
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.core.models import Tenant
from apps.domains.messaging.models import NotificationPreviewToken


class PreviewTokenRetentionTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            code="preview-retention",
            name="Preview Retention",
            is_active=True,
        )

    def _token(self, *, expires_at):
        return NotificationPreviewToken.objects.create(
            token=uuid4(),
            tenant=self.tenant,
            notification_type="clinic_reminder",
            session_type="manual",
            session_id=0,
            send_to="parent",
            payload={
                "recipients": [{"phone_raw": "01012345678", "message_body": "PII"}],
            },
            expires_at=expires_at,
        )

    def test_purge_deletes_only_expired_tokens(self):
        expired = self._token(expires_at=timezone.now() - timedelta(seconds=1))
        active = self._token(expires_at=timezone.now() + timedelta(minutes=5))

        stdout = StringIO()
        call_command(
            "purge_expired_notification_preview_tokens",
            batch_size=1,
            stdout=stdout,
        )

        self.assertFalse(NotificationPreviewToken.objects.filter(id=expired.id).exists())
        self.assertTrue(NotificationPreviewToken.objects.filter(id=active.id).exists())
        self.assertIn("expired_preview_tokens_deleted=1", stdout.getvalue())

    def test_dry_run_reports_without_deleting(self):
        expired = self._token(expires_at=timezone.now() - timedelta(seconds=1))

        stdout = StringIO()
        call_command(
            "purge_expired_notification_preview_tokens",
            dry_run=True,
            stdout=stdout,
        )

        self.assertTrue(NotificationPreviewToken.objects.filter(id=expired.id).exists())
        self.assertIn("expired_preview_tokens=1 dry_run=true", stdout.getvalue())

    @patch(
        "apps.domains.messaging.scheduled.process_due_notifications",
        return_value={
            "processed": 0,
            "sent": 0,
            "retried": 0,
            "failed": 0,
            "deferred": 0,
        },
    )
    def test_scheduled_processor_purges_expired_tokens(self, _process_due):
        expired = self._token(expires_at=timezone.now() - timedelta(seconds=1))

        call_command("process_scheduled_notifications", stdout=StringIO())

        self.assertFalse(NotificationPreviewToken.objects.filter(id=expired.id).exists())
