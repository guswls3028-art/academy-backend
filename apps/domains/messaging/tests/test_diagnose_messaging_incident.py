from __future__ import annotations

import json
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.core.models import Tenant
from apps.domains.messaging.models import NotificationLog
from apps.domains.messaging.scheduled import create_notification_outboxes


class DiagnoseMessagingIncidentCommandTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            code="messaging-diagnostic",
            name="Messaging Diagnostic",
            is_active=True,
        )
        self.phone = "010-8836-9576"
        self.outbox = create_notification_outboxes(
            tenant_id=self.tenant.id,
            notifications=[
                {
                    "trigger": "registration_approved_parent",
                    "send_at": timezone.now(),
                    "payload": {
                        "tenant_id": self.tenant.id,
                        "to": self.phone,
                        "text": "credential body must stay private",
                        "message_mode": "alimtalk",
                        "origin_type": "excel_import",
                        "origin_id": "excel-job-private-trace",
                    },
                }
            ],
        )[0]
        NotificationLog.objects.create(
            tenant=self.tenant,
            source_tenant=self.tenant,
            success=True,
            status="sent",
            message_mode="alimtalk",
            business_idempotency_key=self.outbox.business_idempotency_key,
            recipient_fingerprint=self.outbox.recipient_fingerprint,
            origin_type=self.outbox.origin_type,
            origin_id=self.outbox.origin_id,
            notification_type="registration_approved_parent",
            provider_message_id="provider-secret-id",
        )

    def test_recipient_and_origin_lookup_outputs_only_aggregates(self):
        output = StringIO()

        call_command(
            "diagnose_messaging_incident",
            tenant_id=self.tenant.id,
            recipient=self.phone,
            origin_id=self.outbox.origin_id,
            stdout=output,
        )

        raw = output.getvalue()
        report = json.loads(raw)
        self.assertEqual(report["outbox"]["total"], 1)
        self.assertEqual(report["delivery_log"]["total"], 1)
        self.assertEqual(report["linkage"]["shared_business_keys"], 1)
        self.assertNotIn("01088369576", raw.replace("-", ""))
        self.assertNotIn("credential body must stay private", raw)
        self.assertNotIn("provider-secret-id", raw)
        self.assertNotIn("excel-job-private-trace", raw)

    @patch(
        "apps.domains.messaging.services.solapi_client.get_solapi_client"
    )
    def test_provider_lookup_reports_sanitized_delivery_counts(self, get_client):
        message = SimpleNamespace(
            type=SimpleNamespace(value="ATA"),
            status_code="2000",
            kakao_options=SimpleNamespace(disable_sms=True),
        )
        get_client.return_value.get_messages.return_value = SimpleNamespace(
            message_list={"provider-secret-id": message},
            next_key=None,
        )
        output = StringIO()

        call_command(
            "diagnose_messaging_incident",
            tenant_id=self.tenant.id,
            recipient=self.phone,
            provider=True,
            stdout=output,
        )

        raw = output.getvalue()
        report = json.loads(raw)
        self.assertEqual(report["provider"]["status"], "ok")
        self.assertEqual(report["provider"]["total"], 1)
        self.assertEqual(report["provider"]["types"], {"ATA": 1})
        self.assertEqual(report["provider"]["kakao_disable_sms"]["true"], 1)
        self.assertNotIn("provider-secret-id", raw)
        self.assertNotIn("01088369576", raw.replace("-", ""))
