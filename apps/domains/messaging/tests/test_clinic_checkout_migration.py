from importlib import import_module

from django.apps import apps
from django.test import TestCase

from apps.core.models import Tenant
from apps.domains.messaging.models import AutoSendConfig


class ClinicCheckoutProvisionMigrationTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            code="checkout-provision",
            name="하원 복구 학원",
            is_active=True,
        )
        self.migration = import_module(
            "apps.domains.messaging.migrations.0040_provision_clinic_checkout_notifications"
        )

    def test_creates_enabled_checkout_config_and_template(self):
        self.migration.provision_clinic_checkout(apps, None)

        config = AutoSendConfig.objects.select_related("template").get(
            tenant=self.tenant,
            trigger="clinic_check_out",
        )
        self.assertTrue(config.enabled)
        self.assertEqual(config.message_mode, "alimtalk")
        self.assertEqual(config.template.category, "clinic")
        self.assertEqual(config.template.body, "클리닉에서 하원하였습니다.")

    def test_preserves_existing_disabled_preference(self):
        config = AutoSendConfig.objects.create(
            tenant=self.tenant,
            trigger="clinic_check_out",
            enabled=False,
            message_mode="alimtalk",
        )

        self.migration.provision_clinic_checkout(apps, None)

        config.refresh_from_db()
        self.assertFalse(config.enabled)
        self.assertIsNotNone(config.template_id)
