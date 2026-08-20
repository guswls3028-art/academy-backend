from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.core.models import Program, Tenant, TenantMembership
from apps.core.models.user import user_internal_username


ONBOARDING_SETTINGS = {
    "ALLOWED_HOSTS": ["movementhui.com", "www.movementhui.com"],
    "CORS_ALLOWED_ORIGINS": [
        "https://movementhui.com",
        "https://www.movementhui.com",
    ],
    "CSRF_TRUSTED_ORIGINS": [
        "https://movementhui.com",
        "https://www.movementhui.com",
    ],
    "BILLING_EXEMPT_TENANT_IDS": set(),
}


@override_settings(**ONBOARDING_SETTINGS)
class AuditTenantOnboardingCommandTests(TestCase):
    def setUp(self):
        call_command(
            "provision_tenant",
            "movementhui",
            tenant_id=10,
            name="이동휘원소 과학연구소",
            domain="movementhui.com",
            login_title="이동휘원소",
            login_subtitle="과학연구소",
            window_title="이동휘원소 과학연구소",
            logo_url="/tenants/movementhui/logo.png",
            primary_color="#1a253b",
            stdout=StringIO(),
        )
        self.tenant = Tenant.objects.get(code="movementhui")
        self.program = Program.objects.get(tenant=self.tenant)
        today = timezone.localdate()
        self.program.subscription_started_at = today
        self.program.subscription_expires_at = today + timedelta(days=30)
        self.program.next_billing_at = today + timedelta(days=30)
        self.program.save(
            update_fields=(
                "subscription_started_at",
                "subscription_expires_at",
                "next_billing_at",
            )
        )
        User = get_user_model()
        self.owner = User.objects.create_user(
            username=user_internal_username(self.tenant, "owner"),
            password="test1234",
            tenant=self.tenant,
            is_active=True,
        )
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.owner,
            role="owner",
            is_active=True,
        )

    def _audit(self, **overrides):
        options = {
            "tenant_id": 10,
            "domain": "movementhui.com",
            "billing_mode": "contract",
            "messaging_mode": "disabled",
            "require_owner": True,
            "stdout": StringIO(),
            "stderr": StringIO(),
        }
        options.update(overrides)
        call_command("audit_tenant_onboarding", "movementhui", **options)
        return options["stdout"].getvalue(), options["stderr"].getvalue()

    def test_complete_tenant_passes(self):
        stdout, stderr = self._audit()

        self.assertIn("[PASS] runtime.cors", stdout)
        self.assertIn("[PASS] billing.access", stdout)
        self.assertIn("[PASS] owner.ready", stdout)
        self.assertIn("[PASS] owner.credential_ready", stdout)
        self.assertIn("TENANT_ONBOARDING_AUDIT_PASS", stdout)
        self.assertEqual(stderr, "")

    def test_owner_handoff_requires_forced_password_change_completion(self):
        self.owner.must_change_password = True
        self.owner.save(update_fields=["must_change_password"])

        stdout = StringIO()
        stderr = StringIO()
        with self.assertRaisesMessage(
            CommandError,
            "keys=owner.handoff_complete",
        ):
            self._audit(
                require_owner_handoff=True,
                stdout=stdout,
                stderr=stderr,
            )

        self.assertIn("[PASS] owner.credential_ready", stdout.getvalue())
        self.assertIn("[FAIL] owner.handoff_complete", stderr.getvalue())

        self.owner.must_change_password = False
        self.owner.save(update_fields=["must_change_password"])
        stdout, _ = self._audit(require_owner_handoff=True)
        self.assertIn("[PASS] owner.handoff_complete", stdout)

    def test_owner_without_usable_password_fails_readiness(self):
        self.owner.set_unusable_password()
        self.owner.save(update_fields=["password"])

        with self.assertRaisesMessage(
            CommandError,
            "keys=owner.credential_ready",
        ):
            self._audit()

    def test_missing_origin_and_unsafe_activation_fail_closed(self):
        self.tenant.messaging_is_active = True
        self.tenant.save(update_fields=["messaging_is_active"])

        stdout = StringIO()
        stderr = StringIO()
        with override_settings(
            CORS_ALLOWED_ORIGINS=["https://movementhui.com"],
        ):
            with self.assertRaisesMessage(
                CommandError,
                "keys=runtime.cors,messaging.activation",
            ):
                self._audit(stdout=stdout, stderr=stderr)

        self.assertIn("[FAIL] runtime.cors", stderr.getvalue())
        self.assertIn("[FAIL] messaging.activation", stderr.getvalue())

    @override_settings(BILLING_EXEMPT_TENANT_IDS={10})
    def test_exempt_tenant_passes_without_owner_handoff(self):
        self.program.subscription_started_at = None
        self.program.subscription_expires_at = None
        self.program.next_billing_at = None
        self.program.save(
            update_fields=(
                "subscription_started_at",
                "subscription_expires_at",
                "next_billing_at",
            )
        )
        TenantMembership.objects.all().delete()

        stdout, _ = self._audit(
            billing_mode="exempt",
            require_owner=False,
        )

        self.assertIn("[PASS] owner.not_duplicated", stdout)
        self.assertIn("[PASS] billing.access", stdout)
        self.assertIn("TENANT_ONBOARDING_AUDIT_PASS", stdout)
