from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.core.models import PendingPasswordReset, Tenant, TenantMembership
from apps.core.models.user import user_internal_username
from apps.core.services.password import create_pending_password_reset


class FixUserPasswordCommandTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Password Operator", code="password-operator")

    def test_existing_user_reset_invalidates_sessions_and_pending_reset(self):
        user = get_user_model().objects.create_user(
            username=user_internal_username(self.tenant, "owner1"),
            password="old-password",
            tenant=self.tenant,
            token_version=4,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=user, role="owner")
        create_pending_password_reset(user, "pending123")

        call_command(
            "fix_user_password",
            username="owner1",
            password=" operator-reset-123 ",
            tenant_code=self.tenant.code,
            stdout=StringIO(),
        )

        user.refresh_from_db()
        self.assertTrue(user.check_password(" operator-reset-123 "))
        self.assertFalse(user.check_password("operator-reset-123"))
        self.assertTrue(user.must_change_password)
        self.assertEqual(user.token_version, 5)
        self.assertFalse(PendingPasswordReset.objects.filter(user=user).exists())

    def test_new_user_starts_with_required_password_change(self):
        call_command(
            "fix_user_password",
            username="owner2",
            password="operator-reset-456",
            tenant_code=self.tenant.code,
            stdout=StringIO(),
        )

        user = get_user_model().objects.get(
            username=user_internal_username(self.tenant, "owner2")
        )
        self.assertTrue(user.check_password("operator-reset-456"))
        self.assertTrue(user.must_change_password)
        self.assertTrue(
            TenantMembership.objects.filter(
                tenant=self.tenant,
                user=user,
                role="owner",
                is_active=True,
            ).exists()
        )

    def test_missing_tenant_fails_instead_of_reporting_success(self):
        with self.assertRaises(CommandError):
            call_command(
                "fix_user_password",
                username="owner3",
                password="temporary-password",
                tenant_code="missing-tenant",
            )

    def test_cleanup_bare_rolls_back_when_replacement_fails(self):
        User = get_user_model()
        bare = User.objects.create_user(username="owner4", password="bare-password")
        bare_id = bare.pk
        internal = User.objects.create_user(
            username=user_internal_username(self.tenant, "owner4"),
            password="internal-password",
            tenant=self.tenant,
        )

        with patch(
            "academy.adapters.db.django.repositories_core.membership_ensure_active",
            side_effect=RuntimeError("membership failure"),
        ):
            with self.assertRaises(RuntimeError):
                call_command(
                    "fix_user_password",
                    username="owner4",
                    password=" reset with spaces ",
                    tenant_code=self.tenant.code,
                    cleanup_bare=True,
                    stdout=StringIO(),
                )

        self.assertTrue(User.objects.filter(pk=bare_id).exists())
        internal.refresh_from_db()
        self.assertTrue(internal.check_password("internal-password"))
