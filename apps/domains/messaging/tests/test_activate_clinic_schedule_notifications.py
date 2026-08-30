from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.core.models import Tenant
from apps.domains.messaging.models import AutoSendConfig, MessageTemplate
from apps.domains.messaging.management.commands.activate_clinic_schedule_notifications import (
    TARGET_TRIGGERS,
)


class ActivateClinicScheduleNotificationsCommandTest(TestCase):
    def setUp(self):
        self.active = Tenant.objects.create(
            code="clinic-active",
            name="Clinic Active",
            is_active=True,
            messaging_is_active=True,
        )
        self.messaging_off = Tenant.objects.create(
            code="clinic-off",
            name="Clinic Off",
            is_active=True,
            messaging_is_active=False,
        )
        self.custom_template = MessageTemplate.objects.create(
            tenant=self.active,
            name="직접 편집한 예약 안내",
            category="clinic",
            subject="사용자 제목",
            body="사용자 본문",
            is_system=False,
        )
        self.created_config = AutoSendConfig.objects.create(
            tenant=self.active,
            trigger="clinic_reservation_created",
            template=self.custom_template,
            enabled=False,
            message_mode="alimtalk",
        )
        self.other_config = AutoSendConfig.objects.create(
            tenant=self.active,
            trigger="clinic_reminder",
            template=self.custom_template,
            enabled=False,
            message_mode="alimtalk",
            minutes_before=45,
        )

    def test_dry_run_changes_nothing(self):
        output = StringIO()

        call_command("activate_clinic_schedule_notifications", stdout=output)

        self.created_config.refresh_from_db()
        self.assertFalse(self.created_config.enabled)
        self.assertEqual(
            AutoSendConfig.objects.filter(
                tenant=self.active,
                trigger__in=TARGET_TRIGGERS,
            ).count(),
            1,
        )
        self.assertIn("DRY-RUN: 변경 없음", output.getvalue())

    def test_apply_enables_only_three_triggers_for_messaging_active_tenants(self):
        call_command(
            "activate_clinic_schedule_notifications",
            "--apply",
            stdout=StringIO(),
        )

        target_configs = AutoSendConfig.objects.filter(
            tenant=self.active,
            trigger__in=TARGET_TRIGGERS,
        ).select_related("template")
        self.assertEqual(target_configs.count(), 3)
        self.assertTrue(all(config.enabled for config in target_configs))
        self.assertTrue(all(config.message_mode == "alimtalk" for config in target_configs))

        self.created_config.refresh_from_db()
        self.assertEqual(self.created_config.template_id, self.custom_template.id)
        self.custom_template.refresh_from_db()
        self.assertEqual(self.custom_template.subject, "사용자 제목")
        self.assertEqual(self.custom_template.body, "사용자 본문")
        self.assertFalse(self.custom_template.is_system)

        self.other_config.refresh_from_db()
        self.assertFalse(self.other_config.enabled)
        self.assertEqual(self.other_config.minutes_before, 45)
        self.assertFalse(
            AutoSendConfig.objects.filter(
                tenant=self.messaging_off,
                trigger__in=TARGET_TRIGGERS,
            ).exists()
        )

        call_command(
            "activate_clinic_schedule_notifications",
            "--apply",
            stdout=StringIO(),
        )
        self.assertEqual(
            AutoSendConfig.objects.filter(
                tenant=self.active,
                trigger__in=TARGET_TRIGGERS,
            ).count(),
            3,
        )

    def test_non_alimtalk_target_mode_fails_before_any_change(self):
        self.created_config.message_mode = "sms"
        self.created_config.save(update_fields=["message_mode", "updated_at"])

        with self.assertRaises(CommandError):
            call_command(
                "activate_clinic_schedule_notifications",
                "--apply",
                stdout=StringIO(),
            )

        self.created_config.refresh_from_db()
        self.assertFalse(self.created_config.enabled)
        self.assertEqual(
            AutoSendConfig.objects.filter(
                tenant=self.active,
                trigger__in=TARGET_TRIGGERS,
            ).count(),
            1,
        )
