from __future__ import annotations

from django.test import TestCase

from apps.core.models import Tenant
from apps.domains.messaging.effective_templates import resolve_effective_template_status
from apps.domains.messaging.models import AutoSendConfig, MessageTemplate
from apps.domains.messaging.serializers import AutoSendConfigSerializer


class EffectiveTemplateStatusTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            code="effective-msg",
            name="Effective Messaging",
            is_active=True,
        )

    def test_unified_trigger_is_effectively_approved_even_if_linked_template_pending(self):
        template = MessageTemplate.objects.create(
            tenant=self.tenant,
            name="Clinic reminder body",
            category=MessageTemplate.Category.DEFAULT,
            subject="Clinic",
            body="Clinic #{student}",
            solapi_template_id="legacy-pending",
            solapi_status="PENDING",
        )
        config = AutoSendConfig.objects.create(
            tenant=self.tenant,
            trigger="clinic_reminder",
            template=template,
            enabled=True,
            message_mode="alimtalk",
        )

        effective = resolve_effective_template_status(config)
        data = AutoSendConfigSerializer(config).data

        self.assertTrue(effective.is_approved)
        self.assertEqual(effective.source, "unified")
        self.assertEqual(data["template_solapi_status"], "PENDING")
        self.assertEqual(data["effective_template_solapi_status"], "APPROVED")
        self.assertEqual(data["effective_template_source"], "unified")
        self.assertTrue(data["effective_template_is_approved"])

    def test_non_unified_trigger_uses_linked_template_status(self):
        template = MessageTemplate.objects.create(
            tenant=self.tenant,
            name="Matchup report",
            category=MessageTemplate.Category.DEFAULT,
            subject="Matchup",
            body="Matchup #{student}",
            solapi_template_id="tenant-pending",
            solapi_status="PENDING",
        )
        config = AutoSendConfig.objects.create(
            tenant=self.tenant,
            trigger="matchup_report_submitted",
            template=template,
            enabled=True,
            message_mode="alimtalk",
        )

        effective = resolve_effective_template_status(config)

        self.assertFalse(effective.is_approved)
        self.assertEqual(effective.source, "tenant_template")
        self.assertEqual(effective.solapi_template_id, "tenant-pending")
        self.assertEqual(effective.solapi_status, "PENDING")
