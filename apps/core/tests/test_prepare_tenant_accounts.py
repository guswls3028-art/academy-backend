import json
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.core.models import OpsAuditLog, Program, Tenant, TenantMembership
from apps.domains.students.models import Student


@override_settings(
    ALLOWED_HOSTS=["qa.example.com", "www.qa.example.com"],
    CORS_ALLOWED_ORIGINS=["https://qa.example.com", "https://www.qa.example.com"],
    CSRF_TRUSTED_ORIGINS=["https://qa.example.com", "https://www.qa.example.com"],
    BILLING_EXEMPT_TENANT_IDS=set(),
)
class PrepareTenantAccountsTests(TestCase):
    def setUp(self):
        call_command(
            "provision_tenant",
            "qa-school",
            tenant_id=10,
            name="QA",
            domain="qa.example.com",
            login_title="QA",
            login_subtitle="School",
            window_title="QA",
            logo_url="/qa.png",
            primary_color="#112233",
            stdout=StringIO(),
        )
        self.tenant = Tenant.objects.get(pk=10)
        self.tenant.messaging_is_active = False
        self.tenant.save(update_fields=["messaging_is_active"])
        today = timezone.localdate()
        Program.objects.filter(tenant=self.tenant).update(
            subscription_started_at=today,
            subscription_expires_at=today + timedelta(days=30),
            next_billing_at=today + timedelta(days=30),
        )
        self.body = {
            "owner": {"username": "qa-owner", "password": "QA-owner-test", "name": "QA Owner", "phone": "01000000001"},
            "student": {
                "ps_number": "qa-student",
                "initial_password": "QA-student-test",
                "name": "QA Student",
                "parent_phone": "01000000002",
            },
        }

    def run_accounts(self, **overrides):
        output = StringIO()
        options = dict(
            tenant_id=10,
            domain="qa.example.com",
            billing_mode="contract",
            messaging_mode="disabled",
            stdout=output,
            stderr=output,
        )
        options.update(overrides)
        with patch("sys.stdin", StringIO(json.dumps(self.body))):
            call_command("prepare_tenant_accounts", "qa-school", **options)
        return output.getvalue().strip()

    def test_real_creation_retry_and_existing_parent_password(self):
        self.assertEqual(self.run_accounts(), "TENANT_ACCOUNTS_READY owner=created student=created")
        users = get_user_model().objects.filter(tenant=self.tenant)
        self.assertEqual(users.count(), 3)
        self.assertTrue(all(u.must_change_password for u in users))
        self.assertEqual(
            set(TenantMembership.objects.filter(tenant=self.tenant).values_list("role", flat=True)),
            {"owner", "student", "parent"},
        )
        hashes = dict(users.values_list("pk", "password"))
        self.body["owner"]["password"] = "different-owner"
        self.body["student"]["initial_password"] = "different-student"
        self.body["student"]["parent_initial_password"] = "different-parent"
        self.assertEqual(self.run_accounts(), "TENANT_ACCOUNTS_READY owner=existing student=existing")
        self.assertEqual(dict(users.values_list("pk", "password")), hashes)
        self.body["student"].update(ps_number="qa-sibling", name="QA sibling")
        self.assertEqual(self.run_accounts(), "TENANT_ACCOUNTS_READY owner=existing student=created")
        self.assertEqual(dict(users.filter(pk__in=hashes).values_list("pk", "password")), hashes)
        self.assertEqual(
            OpsAuditLog.objects.filter(
                target_tenant=self.tenant, actor_username="management:prepare_tenant_accounts"
            ).count(),
            4,
        )

    def test_bad_student_rolls_back_owner_and_audit(self):
        self.body["student"]["parent_phone"] = "invalid-private-input"
        with self.assertRaisesMessage(CommandError, "TENANT_ACCOUNT_PREPARATION_FAILED"):
            self.run_accounts()
        self.assertFalse(get_user_model().objects.filter(tenant=self.tenant).exists())
        self.assertFalse(OpsAuditLog.objects.filter(target_tenant=self.tenant).exists())

    def test_wrong_target_or_gate_cannot_create(self):
        for options in ({"tenant_id": 11}, {"domain": "other.example.com"}, {"messaging_mode": "approved"}):
            with self.subTest(options=options), self.assertRaises(CommandError):
                self.run_accounts(**options)
        self.assertFalse(get_user_model().objects.filter(tenant=self.tenant).exists())

    def test_duplicate_owner_or_conflicting_student_cannot_mutate(self):
        self.run_accounts()
        self.body["owner"]["username"] = "another-owner"
        with self.assertRaises(CommandError):
            self.run_accounts()
        self.body["owner"]["username"] = "qa-owner"
        self.body["student"]["name"] = "Changed"
        with self.assertRaises(CommandError):
            self.run_accounts()
        self.assertEqual(Student.objects.get(tenant=self.tenant).name, "QA Student")

    def test_unusable_existing_guardian_is_never_reset(self):
        self.run_accounts()
        student = Student.objects.get(tenant=self.tenant)
        guardian = student.parent.user
        guardian.set_unusable_password()
        guardian.save(update_fields=["password"])
        original = guardian.password
        self.body["student"].update(ps_number="qa-sibling", name="QA Sibling")
        with self.assertRaises(CommandError):
            self.run_accounts()
        guardian.refresh_from_db()
        self.assertEqual(guardian.password, original)
        self.assertEqual(Student.objects.filter(tenant=self.tenant).count(), 1)

    def test_owner_only_and_duplicate_json_keys(self):
        del self.body["student"]
        self.assertEqual(self.run_accounts(), "TENANT_ACCOUNTS_READY owner=created student=not_requested")
        with patch("sys.stdin", StringIO('{"owner":{},"owner":{}}')), self.assertRaises(CommandError):
            call_command(
                "prepare_tenant_accounts",
                "qa-school",
                tenant_id=10,
                domain="qa.example.com",
                billing_mode="contract",
                messaging_mode="disabled",
            )

    def test_owner_without_profile_uses_login_for_empty_tenant_display(self):
        self.body = {"owner": {"username": "qa-owner", "password": "QA-initial"}}
        self.tenant.owner_name = ""
        self.tenant.save(update_fields=["owner_name"])
        self.run_accounts()
        owner = get_user_model().objects.get(tenant=self.tenant)
        self.tenant.refresh_from_db()
        self.assertEqual((owner.name, owner.phone, self.tenant.owner_name), ("", "", "qa-owner"))
        self.body["owner"].update(name=None, phone=" ")
        self.assertIn("owner=existing", self.run_accounts())

    def test_existing_owner_omitted_or_blank_profile_is_preserved_and_conflict_refused(self):
        del self.body["student"]
        self.run_accounts()
        user = get_user_model().objects.get(tenant=self.tenant)
        self.tenant.refresh_from_db()
        saved = (user.name, user.phone, user.password, self.tenant.owner_name)
        self.body["owner"] = {"username": "qa-owner", "password": "different"}
        self.assertIn("owner=existing", self.run_accounts())
        self.body["owner"].update(name="  ", phone=None)
        self.assertIn("owner=existing", self.run_accounts())
        for key, value in (("name", "another"), ("phone", "01000000003")):
            with self.subTest(field=key), self.assertRaises(CommandError):
                self.body["owner"].update({key: value})
                self.run_accounts()
            del self.body["owner"][key]
        user.refresh_from_db()
        self.tenant.refresh_from_db()
        self.assertEqual((user.name, user.phone, user.password, self.tenant.owner_name), saved)
