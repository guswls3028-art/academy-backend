from __future__ import annotations

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.core.models import Tenant, TenantMembership
from apps.core.models.user import user_internal_username
from apps.domains.parents.models import Parent
from apps.domains.students.models import Student


User = get_user_model()


class EnsureParentAccountsCommandTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Parent repair",
            code="parent-repair",
            is_active=True,
        )
        self.student_user = User.objects.create_user(
            username=user_internal_username(self.tenant, "S10001"),
            password="student-secret",
            tenant=self.tenant,
            name="Student",
        )
        self.student = Student.objects.create(
            tenant=self.tenant,
            user=self.student_user,
            ps_number="S10001",
            name="Student",
            phone="01011112222",
            parent_phone="01033334444",
            omr_code="11112222",
            school_type="HIGH",
            grade=1,
        )

    def _run(self, *args: str) -> str:
        output = StringIO()
        call_command(
            "ensure_parent_accounts_for_students",
            "--tenant",
            self.tenant.code,
            *args,
            stdout=output,
        )
        return output.getvalue()

    def test_default_is_read_only(self):
        output = self._run()

        self.assertIn("mode=dry-run candidates=1", output)
        self.assertFalse(Parent.objects.filter(tenant=self.tenant).exists())

    def test_execute_requires_exact_tenant_confirmation(self):
        with self.assertRaisesMessage(CommandError, "confirmation_required"):
            self._run("--execute", "--confirm", "wrong")

        self.assertFalse(Parent.objects.filter(tenant=self.tenant).exists())

    def test_execute_creates_only_the_missing_parent_account(self):
        output = self._run(
            "--execute",
            "--confirm",
            self.tenant.code,
        )

        parent = Parent.objects.get(
            tenant=self.tenant,
            phone=self.student.parent_phone,
        )
        self.assertTrue(parent.user.check_password("4444"))
        self.assertTrue(parent.user.must_change_password)
        self.assertTrue(
            TenantMembership.objects.filter(
                tenant=self.tenant,
                user=parent.user,
                role="parent",
                is_active=True,
            ).exists()
        )
        self.assertIn("created=1 existing_passwords_changed=0", output)

    def test_existing_parent_password_is_never_changed(self):
        parent_user = User.objects.create_user(
            username=f"p_{self.tenant.id}_{self.student.parent_phone}",
            password="parent-secret",
            tenant=self.tenant,
            name="Parent",
        )
        Parent.objects.create(
            tenant=self.tenant,
            user=parent_user,
            name="Parent",
            phone=self.student.parent_phone,
        )

        output = self._run(
            "--execute",
            "--confirm",
            self.tenant.code,
        )

        parent_user.refresh_from_db()
        self.assertTrue(parent_user.check_password("parent-secret"))
        self.assertFalse(parent_user.check_password("student-secret"))
        self.assertIn("candidates=0", output)
