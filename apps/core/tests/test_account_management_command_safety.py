from __future__ import annotations

from io import StringIO
import json
import os
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.core.models import Program, Tenant, TenantDomain, TenantMembership
from apps.core.models.user import user_internal_username
from apps.core.services.password import create_pending_password_reset
from apps.core.management.commands.ensure_dev_user import Command as EnsureDevUserCommand


class AccountManagementCommandSafetyTests(TestCase):
    def test_ensure_dev_user_requires_environment_secret_and_never_prints_it(self):
        password = " local secret with spaces "
        output = StringIO()

        with patch.dict(os.environ, {"ACADEMY_DEV_USER_PASSWORD": password}):
            call_command(
                "ensure_dev_user",
                tenant="local-command",
                username="local-admin",
                stdout=output,
            )

        user = get_user_model().objects.get(
            username=user_internal_username(
                Tenant.objects.get(code="local-command"),
                "local-admin",
            )
        )
        self.assertTrue(user.check_password(password))
        self.assertNotIn(password, output.getvalue())

    def test_ensure_dev_user_reset_invalidates_sessions_and_pending_password(self):
        with patch.dict(
            os.environ,
            {"ACADEMY_DEV_USER_PASSWORD": "first-local-password"},
        ):
            call_command(
                "ensure_dev_user",
                tenant="local-reset",
                username="local-admin",
                stdout=StringIO(),
            )

        tenant = Tenant.objects.get(code="local-reset")
        user = get_user_model().objects.get(
            username=user_internal_username(tenant, "local-admin")
        )
        create_pending_password_reset(user, "pending-password")
        original_token_version = user.token_version

        with patch.dict(
            os.environ,
            {"ACADEMY_DEV_USER_PASSWORD": "second-local-password"},
        ):
            call_command(
                "ensure_dev_user",
                tenant="local-reset",
                username="local-admin",
                stdout=StringIO(),
            )

        user.refresh_from_db()
        self.assertTrue(user.check_password("second-local-password"))
        self.assertEqual(user.token_version, original_token_version + 1)
        self.assertFalse(hasattr(user, "pending_password_reset"))

    def test_ensure_dev_user_rejects_missing_environment_secret(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesMessage(CommandError, "ACADEMY_DEV_USER_PASSWORD"):
                call_command(
                    "ensure_dev_user",
                    tenant="missing-secret",
                    stdout=StringIO(),
                )

    def test_ensure_dev_user_has_no_remote_database_override(self):
        parser = EnsureDevUserCommand().create_parser("manage.py", "ensure_dev_user")

        self.assertNotIn("--allow-remote-db", parser.format_help())

    def test_ensure_dev_user_always_rejects_remote_database(self):
        with (
            patch.dict(
                settings.DATABASES["default"],
                {
                    "ENGINE": "django.db.backends.postgresql",
                    "HOST": "database.internal",
                },
            ),
            patch.dict(
                os.environ,
                {"ACADEMY_DEV_USER_PASSWORD": "local-only-password"},
            ),
        ):
            with self.assertRaisesMessage(CommandError, "non-local database"):
                call_command(
                    "ensure_dev_user",
                    tenant="remote-refused",
                    username="local-admin",
                    stdout=StringIO(),
                )

    def test_dump_is_tenant_scoped_and_excludes_credentials_and_pii(self):
        tenant = Tenant.objects.create(
            code="safe-dump",
            name="Sensitive Academy Name",
            is_active=True,
        )
        program = Program.objects.get(tenant=tenant)
        program.billing_email = "billing-sensitive@example.com"
        program.save(update_fields=["billing_email"])
        domain = TenantDomain.objects.get(tenant=tenant)
        domain.host = "safe-dump.localhost"
        domain.save(update_fields=["host"])
        login_identifier = "01012345678"
        user = get_user_model().objects.create_user(
            username=user_internal_username(tenant, login_identifier),
            password="never-print-this-password",
            tenant=tenant,
            name="Sensitive Person Name",
            email="person-sensitive@example.com",
            phone="01012345678",
        )
        TenantMembership.objects.create(
            tenant=tenant,
            user=user,
            role="admin",
            is_active=True,
        )

        output = StringIO()
        call_command(
            "dump_tenant_and_user",
            tenant_code=tenant.code,
            username=login_identifier,
            stdout=output,
        )
        dumped = output.getvalue()
        payload = json.loads(dumped)

        self.assertEqual(payload["user"]["id"], user.id)
        self.assertNotIn("username", payload["user"])
        self.assertIn('"host": "safe-dump.localhost"', dumped)
        for sensitive_value in (
            login_identifier,
            user.password,
            "never-print-this-password",
            "Sensitive Academy Name",
            "Sensitive Person Name",
            "billing-sensitive@example.com",
            "person-sensitive@example.com",
            "01012345678",
        ):
            self.assertNotIn(sensitive_value, dumped)

    def test_dump_fails_closed_for_user_outside_tenant(self):
        tenant = Tenant.objects.create(code="dump-one", name="One", is_active=True)
        other = Tenant.objects.create(code="dump-two", name="Two", is_active=True)
        login_identifier = "01055556666"
        user = get_user_model().objects.create_user(
            username=user_internal_username(other, login_identifier),
            password="test-only-password",
            tenant=other,
        )
        TenantMembership.objects.create(
            tenant=other,
            user=user,
            role="admin",
            is_active=True,
        )

        with self.assertRaises(CommandError) as raised:
            call_command(
                "dump_tenant_and_user",
                tenant_code=tenant.code,
                username=login_identifier,
                stdout=StringIO(),
            )

        self.assertNotIn(login_identifier, str(raised.exception))
