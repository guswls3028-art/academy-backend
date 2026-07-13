from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from apps.core.models import Tenant
from apps.domains.messaging.models import ScheduledNotification


class MessagingDeliveryStatePreflightTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            code="messaging-preflight",
            name="Messaging Preflight",
            is_active=True,
        )

    def test_reports_zero_for_object_payloads(self):
        ScheduledNotification.objects.create(
            tenant=self.tenant,
            trigger="valid",
            send_at=timezone.now(),
            payload={"to": "01012345678", "text": "valid"},
        )
        stdout = StringIO()

        call_command("preflight_messaging_delivery_state", stdout=stdout)

        self.assertIn("malformed_payload_count=0", stdout.getvalue())

    def test_fails_with_bounded_ids_and_preserves_payload(self):
        malformed = ScheduledNotification.objects.create(
            tenant=self.tenant,
            trigger="malformed",
            send_at=timezone.now(),
            payload=["legacy", "payload"],
        )

        with self.assertRaisesRegex(
            CommandError,
            f"sample_ids=\\[{malformed.id}\\]",
        ):
            call_command("preflight_messaging_delivery_state")

        malformed.refresh_from_db()
        self.assertEqual(malformed.payload, ["legacy", "payload"])
