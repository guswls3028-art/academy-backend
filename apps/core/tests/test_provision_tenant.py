import os
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.core.models import Program, Tenant, TenantDomain, TenantMembership
from apps.core.models.user import user_display_username


class ProvisionTenantCommandTests(TestCase):
    def _call(self, **overrides):
        options = {
            "tenant_id": 10,
            "name": "이동휘원소 과학연구소",
            "domain": "movementhui.com",
            "login_title": "이동휘원소",
            "login_subtitle": "과학연구소",
            "window_title": "이동휘원소 과학연구소",
            "logo_url": "/tenants/movementhui/logo.png",
            "primary_color": "#1a253b",
            "owner_username": "movement-owner",
            "owner_name": "이동휘",
            "stdout": StringIO(),
        }
        options.update(overrides)
        call_command("provision_tenant", "movementhui", **options)
        return options["stdout"].getvalue()

    def test_provisions_core_rows_and_owner(self):
        with patch.dict(os.environ, {"TENANT_OWNER_INITIAL_PASSWORD": "test1234"}):
            output = self._call()

        tenant = Tenant.objects.get(code="movementhui")
        self.assertEqual(tenant.id, 10)
        self.assertEqual(tenant.owner_name, "이동휘")
        self.assertEqual(
            set(
                TenantDomain.objects.filter(tenant=tenant).values_list(
                    "host", "is_primary"
                )
            ),
            {
                ("movementhui", False),
                ("movementhui.com", True),
                ("www.movementhui.com", False),
            },
        )
        program = Program.objects.get(tenant=tenant)
        self.assertEqual(program.display_name, "이동휘원소 과학연구소")
        self.assertEqual(program.brand_key, "movementhui")
        self.assertEqual(program.ui_config["login_title"], "이동휘원소")
        self.assertEqual(program.ui_config["primary_color"], "#1a253b")
        self.assertTrue(program.feature_flags["student_app_enabled"])
        self.assertTrue(program.feature_flags["admin_enabled"])

        membership = TenantMembership.objects.get(tenant=tenant, role="owner")
        self.assertEqual(user_display_username(membership.user), "movement-owner")
        self.assertTrue(membership.user.must_change_password)
        self.assertTrue(membership.user.check_password("test1234"))
        self.assertIn("APPLIED: tenant=movementhui id=10", output)

    def test_rerun_preserves_manual_branding_and_does_not_require_password(self):
        with patch.dict(os.environ, {"TENANT_OWNER_INITIAL_PASSWORD": "test1234"}):
            self._call()
        program = Program.objects.get(tenant__code="movementhui")
        program.ui_config = {
            **program.ui_config,
            "login_title": "원장 수동 제목",
            "custom_key": "keep",
        }
        program.save(update_fields=["ui_config"])

        with patch.dict(os.environ, {}, clear=True):
            self._call()

        program.refresh_from_db()
        self.assertEqual(program.ui_config["login_title"], "원장 수동 제목")
        self.assertEqual(program.ui_config["custom_key"], "keep")
        self.assertEqual(Tenant.objects.filter(code="movementhui").count(), 1)
        self.assertEqual(
            TenantDomain.objects.filter(tenant__code="movementhui").count(),
            3,
        )

    def test_domain_conflict_rolls_back_new_tenant(self):
        other = Tenant.objects.create(code="other", name="Other")
        TenantDomain.objects.filter(tenant=other, is_primary=True).update(
            is_primary=False
        )
        TenantDomain.objects.create(
            tenant=other,
            host="movementhui.com",
            is_primary=True,
            is_active=True,
        )

        with patch.dict(os.environ, {"TENANT_OWNER_INITIAL_PASSWORD": "test1234"}):
            with self.assertRaises(CommandError):
                self._call()

        self.assertFalse(Tenant.objects.filter(code="movementhui").exists())

    def test_dry_run_rolls_back_everything(self):
        with patch.dict(os.environ, {"TENANT_OWNER_INITIAL_PASSWORD": "test1234"}):
            output = self._call(dry_run=True)

        self.assertIn("DRY RUN: tenant=movementhui id=10", output)
        self.assertFalse(Tenant.objects.filter(code="movementhui").exists())
