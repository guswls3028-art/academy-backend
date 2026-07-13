from datetime import date, timedelta
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.core.models import Tenant


@override_settings(BILLING_EXEMPT_TENANT_IDS=set())
class AuditBillingLifecycleTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Billing Lifecycle Audit",
            code="billing_lifecycle_audit",
            is_active=True,
        )
        self.program = self.tenant.program
        self.program.next_billing_at = date.today() + timedelta(days=30)

    def test_strict_rejects_active_status_past_effective_expiry(self):
        self.program.subscription_status = "active"
        self.program.subscription_expires_at = date.today() - timedelta(days=1)
        self.program.save(
            update_fields=[
                "subscription_status",
                "subscription_expires_at",
                "next_billing_at",
            ]
        )
        output = StringIO()

        with self.assertRaisesRegex(CommandError, "billing_audit_strict_failed"):
            call_command("audit_billing_fields", strict=True, stdout=output)

        self.assertIn("active_subscription_past_expiry", output.getvalue())

    @override_settings(BILLING_GRACE_PERIOD_DAYS=7)
    def test_strict_rejects_grace_status_after_access_window(self):
        self.program.subscription_status = "grace"
        self.program.subscription_expires_at = date.today() - timedelta(days=8)
        self.program.save(
            update_fields=[
                "subscription_status",
                "subscription_expires_at",
                "next_billing_at",
            ]
        )
        output = StringIO()

        with self.assertRaisesRegex(CommandError, "billing_audit_strict_failed"):
            call_command("audit_billing_fields", strict=True, stdout=output)

        self.assertIn("grace_subscription_past_access", output.getvalue())
