from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.core.models import PendingPasswordReset, Tenant
from apps.core.models.user import user_internal_username
from apps.core.services.password import create_pending_password_reset
from apps.domains.parents.models import Parent


class ParentPasswordResetCommandTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Parent Reset", code="parent-reset")
        self.other_tenant = Tenant.objects.create(name="Other", code="parent-reset-other")
        User = get_user_model()
        self.user = User.objects.create_user(
            username=user_internal_username(self.tenant, "01012345678"),
            password="old-password",
            tenant=self.tenant,
            token_version=0,
        )
        self.parent = Parent.objects.create(
            tenant=self.tenant,
            user=self.user,
            name="학생 학부모",
            phone="01012345678",
        )
        self.other_user = User.objects.create_user(
            username=user_internal_username(self.other_tenant, "01087654321"),
            password="other-password",
            tenant=self.other_tenant,
            token_version=0,
        )
        Parent.objects.create(
            tenant=self.other_tenant,
            user=self.other_user,
            name="다른 학부모",
            phone="01087654321",
        )
        create_pending_password_reset(self.user, "pending123")

    def test_default_is_tenant_scoped_dry_run_without_password_suffix_output(self):
        output = StringIO()

        call_command(
            "reset_all_parent_passwords",
            tenant_code=self.tenant.code,
            stdout=output,
        )

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("old-password"))
        self.assertIn("dry-run", output.getvalue())
        self.assertIn("phone=010********", output.getvalue())
        self.assertNotIn("phone=01012345678", output.getvalue())
        self.assertNotIn("phone=010****5678", output.getvalue())

    def test_apply_requires_exact_count_and_invalidates_old_sessions_and_pending_reset(self):
        with self.assertRaises(CommandError):
            call_command(
                "reset_all_parent_passwords",
                tenant_code=self.tenant.code,
                apply=True,
                confirm_count=2,
            )

        call_command(
            "reset_all_parent_passwords",
            tenant_code=self.tenant.code,
            apply=True,
            confirm_count=1,
            stdout=StringIO(),
        )

        self.user.refresh_from_db()
        self.other_user.refresh_from_db()
        self.assertTrue(self.user.check_password("5678"))
        self.assertTrue(self.user.must_change_password)
        self.assertEqual(self.user.token_version, 1)
        self.assertFalse(PendingPasswordReset.objects.filter(user=self.user).exists())
        self.assertTrue(self.other_user.check_password("other-password"))
        self.assertEqual(self.other_user.token_version, 0)
