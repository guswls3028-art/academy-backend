import os
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.core.models import Program, Tenant, TenantMembership


class SetupYmathRealuseScenarioTests(TestCase):
    def _call_command(self, **kwargs):
        out = StringIO()
        with patch.dict(
            os.environ,
            {"YMATH_REALUSE_SCENARIO_PASSWORD": "scenario-test-password"},
        ):
            call_command(
                "setup_ymath_realuse_scenario",
                stdout=out,
                student_count=2,
                session_count=3,
                **kwargs,
            )
        return out.getvalue()

    def test_creates_idempotent_ymath_shaped_scenario(self):
        first = self._call_command()
        second = self._call_command()

        self.assertIn("YMATH_REALUSE_SCENARIO_READY", first)
        self.assertIn("YMATH_REALUSE_SCENARIO_READY", second)
        tenant = Tenant.objects.get(code="qa-ymath-realuse-20260805")
        program = Program.objects.get(tenant=tenant)
        self.assertEqual(program.brand_key, "ymath")
        self.assertFalse(program.feature_flags["section_mode"])
        self.assertEqual(program.feature_flags["clinic_mode"], "remediation")
        self.assertEqual(program.feature_flags["score_output_mode"], "anonymous_billboard")
        self.assertEqual(program.subscription_status, Program.SubscriptionStatus.ACTIVE)
        self.assertEqual(program.subscription_started_at, timezone.localdate())
        self.assertEqual(
            program.subscription_expires_at,
            timezone.localdate() + timedelta(days=365),
        )
        self.assertFalse(program.cancel_at_period_end)
        self.assertTrue(program.is_subscription_active)
        self.assertIn(
            f'"subscription_expires_at": "{program.subscription_expires_at.isoformat()}"',
            second,
        )
        self.assertEqual(tenant.students.count(), 2)
        self.assertEqual(tenant.lectures.count(), 2)
        self.assertEqual(sum(lecture.sessions.count() for lecture in tenant.lectures.all()), 6)
        self.assertEqual(tenant.enrollments.count(), 4)
        self.assertEqual(
            TenantMembership.objects.filter(tenant=tenant, role="admin", is_active=True).count(),
            1,
        )
        User = get_user_model()
        teacher = User.objects.get(username=f"t{tenant.id}_ymath-qa-teacher")
        self.assertTrue(teacher.check_password("scenario-test-password"))

    def test_rejects_non_scenario_tenant_code(self):
        with self.assertRaisesMessage(CommandError, "tenant-code must start"):
            self._call_command(tenant_code="ymath")

    @override_settings(
        DATABASES={"default": {"NAME": "academy_api", "ENGINE": "django.db.backends.postgresql"}},
        R2_AI_BUCKET="academy-ai",
        R2_STORAGE_BUCKET="academy-storage",
        R2_EXCEL_BUCKET="academy-excel",
        R2_ADMIN_BUCKET="academy-admin",
    )
    def test_rejects_production_shaped_runtime(self):
        with self.assertRaisesMessage(CommandError, "isolated development"):
            self._call_command()
