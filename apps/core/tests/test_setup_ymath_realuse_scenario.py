import json
import os
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.core.management.commands.setup_ymath_realuse_scenario import Command
from apps.core.models import Program, Tenant, TenantMembership
from apps.domains.parents.models import Parent
from apps.domains.staffs.models import Staff


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
        self.assertEqual(program.feature_flags["score_summary_column_default"], "exam_wrong")
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
        self.assertEqual(
            TenantMembership.objects.filter(tenant=tenant, role="student", is_active=True).count(),
            2,
        )
        User = get_user_model()
        teacher = User.objects.get(username=f"t{tenant.id}_ymath-qa-teacher")
        self.assertTrue(teacher.check_password("scenario-test-password"))
        student = User.objects.get(username=f"t{tenant.id}_ymath-qa-student-01")
        self.assertTrue(student.check_password("scenario-test-password"))

    def test_rejects_non_scenario_tenant_code(self):
        with self.assertRaisesMessage(CommandError, "tenant-code must start"):
            self._call_command(tenant_code="ymath")

    def test_login_uat_creates_secret_free_ten_by_ten_by_ten_manifest(self):
        out = StringIO()
        secret = "scenario-test-password"
        with patch.dict(
            os.environ,
            {"YMATH_REALUSE_SCENARIO_PASSWORD": secret},
        ):
            call_command(
                "setup_ymath_realuse_scenario",
                stdout=out,
                tenant_code="qa-ymath-realuse-login-uat",
                session_count=1,
                login_uat=True,
                reset=True,
            )

        payload = json.loads(out.getvalue().splitlines()[-1])
        manifest = payload["login_manifest"]
        accounts = manifest["accounts"]
        tenant = Tenant.objects.get(code="qa-ymath-realuse-login-uat")

        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["tenant_code"], tenant.code)
        self.assertEqual(manifest["account_count"], 30)
        self.assertEqual(len(accounts), 30)
        self.assertEqual(len({account["username"] for account in accounts}), 30)
        self.assertEqual(
            {role: sum(account["role"] == role for account in accounts) for role in ("student", "parent", "staff")},
            {"student": 10, "parent": 10, "staff": 10},
        )
        self.assertEqual(tenant.students.count(), 10)
        self.assertEqual(Parent.objects.filter(tenant=tenant).count(), 10)
        self.assertEqual(Staff.objects.filter(tenant=tenant).count(), 10)
        self.assertEqual(
            TenantMembership.objects.filter(tenant=tenant, role="student", is_active=True).count(),
            10,
        )
        self.assertEqual(
            TenantMembership.objects.filter(tenant=tenant, role="parent", is_active=True).count(),
            10,
        )
        self.assertEqual(
            TenantMembership.objects.filter(
                tenant=tenant,
                role="staff",
                is_active=True,
            ).count(),
            10,
        )
        self.assertEqual(
            TenantMembership.objects.filter(tenant=tenant, role="admin", is_active=True).count(),
            1,
        )
        self.assertNotIn(secret, out.getvalue())
        self.assertTrue(all(set(account) == {"role", "username", "landing_path"} for account in accounts))

        parent = Parent.objects.get(tenant=tenant, phone="01099000001")
        self.assertEqual(parent.students.count(), 1)
        self.assertTrue(parent.user.check_password(secret))
        self.assertFalse(parent.user.must_change_password)
        self.assertEqual(parent.user.token_version, 1)

        first_student = tenant.students.get(name="검증학생 01")
        self.assertTrue(first_student.user.check_password(secret))
        self.assertFalse(first_student.user.must_change_password)
        self.assertEqual(first_student.user.token_version, 1)

        first_staff = Staff.objects.get(tenant=tenant, name="로그인 검증 직원 01")
        self.assertTrue(first_staff.user.check_password(secret))
        self.assertFalse(first_staff.user.must_change_password)
        self.assertEqual(first_staff.user.token_version, 1)
        self.assertEqual(
            TenantMembership.objects.get(tenant=tenant, user=first_staff.user).role,
            "staff",
        )

        tenant_id = tenant.id
        user_ids = list(tenant.users.values_list("id", flat=True))
        cleanup_out = StringIO()
        with patch.dict(os.environ, {}, clear=True):
            call_command(
                "setup_ymath_realuse_scenario",
                stdout=cleanup_out,
                tenant_code=tenant.code,
                destroy=True,
            )
        cleanup_payload = json.loads(cleanup_out.getvalue().splitlines()[-1])
        self.assertEqual(cleanup_payload["remaining"], {"tenants": 0, "users": 0})
        self.assertEqual(cleanup_payload["deleted"]["parents"], 10)
        self.assertEqual(cleanup_payload["deleted"]["staffs"], 10)
        self.assertFalse(Tenant.objects.filter(id=tenant_id).exists())
        self.assertFalse(get_user_model().objects.filter(id__in=user_ids).exists())

    def test_login_uat_requires_reset_when_tenant_already_exists(self):
        Tenant.objects.create(code="qa-ymath-realuse-login-existing", name="existing")

        with self.assertRaisesMessage(CommandError, "--reset"):
            self._call_command(
                tenant_code="qa-ymath-realuse-login-existing",
                login_uat=True,
            )

        self.assertEqual(Tenant.objects.filter(code="qa-ymath-realuse-login-existing").count(), 1)

    def test_rejects_empty_or_reserved_teacher_username_before_mutation(self):
        for username in ("", "   ", "ymath-qa-student-01", "YMATH-QA-STAFF-10"):
            tenant_code = "qa-ymath-realuse-invalid-" + str(len(username))
            with self.subTest(username=username):
                with self.assertRaises(CommandError):
                    self._call_command(
                        tenant_code=tenant_code,
                        teacher_username=username,
                        login_uat=True,
                        reset=True,
                    )
                self.assertFalse(Tenant.objects.filter(code=tenant_code).exists())

    def test_reused_user_password_uses_password_service_token_version(self):
        tenant = Tenant.objects.create(code="qa-ymath-realuse-reused-user", name="reuse")
        user = Command._ensure_user(
            tenant=tenant,
            login_username="ymath-qa-student-01",
            password="first-password",
            name="학생",
            is_staff=False,
        )
        first_version = user.token_version

        reused = Command._ensure_user(
            tenant=tenant,
            login_username="ymath-qa-student-01",
            password="second-password",
            name="학생",
            is_staff=False,
        )

        self.assertTrue(reused.check_password("second-password"))
        self.assertEqual(reused.token_version, first_version + 1)
        self.assertFalse(reused.must_change_password)

    def test_login_uat_reset_rolls_back_old_tenant_on_mid_build_error(self):
        tenant_code = "qa-ymath-realuse-login-rollback"
        self._call_command(tenant_code=tenant_code)
        original = Command._ensure_user

        def fail_during_staff_creation(**kwargs):
            if kwargs["login_username"] == "ymath-qa-staff-05":
                raise RuntimeError("forced mid-build failure")
            return original(**kwargs)

        with patch.object(Command, "_ensure_user", side_effect=fail_during_staff_creation):
            with self.assertRaisesMessage(RuntimeError, "forced mid-build failure"):
                self._call_command(
                    tenant_code=tenant_code,
                    login_uat=True,
                    reset=True,
                )

        tenant = Tenant.objects.get(code=tenant_code)
        self.assertEqual(tenant.students.count(), 2)
        self.assertEqual(Parent.objects.filter(tenant=tenant).count(), 0)
        self.assertEqual(Staff.objects.filter(tenant=tenant).count(), 0)

    def test_destroy_removes_exact_scenario_tenant_and_users_without_password(self):
        self._call_command()
        tenant = Tenant.objects.get(code="qa-ymath-realuse-20260805")
        tenant_id = tenant.id
        user_ids = list(tenant.users.values_list("id", flat=True))
        out = StringIO()

        with patch.dict(os.environ, {}, clear=True):
            call_command(
                "setup_ymath_realuse_scenario",
                stdout=out,
                tenant_code="qa-ymath-realuse-20260805",
                destroy=True,
            )

        self.assertIn("YMATH_REALUSE_SCENARIO_DESTROYED", out.getvalue())
        self.assertIn('"remaining": {"tenants": 0, "users": 0}', out.getvalue())
        self.assertFalse(Tenant.objects.filter(id=tenant_id).exists())
        self.assertFalse(get_user_model().objects.filter(id__in=user_ids).exists())

    def test_destroy_is_idempotent_when_scenario_is_absent(self):
        out = StringIO()

        with patch.dict(os.environ, {}, clear=True):
            call_command(
                "setup_ymath_realuse_scenario",
                stdout=out,
                tenant_code="qa-ymath-realuse-20260805",
                destroy=True,
            )

        self.assertIn("YMATH_REALUSE_SCENARIO_ABSENT", out.getvalue())
        self.assertIn('"remaining": {"tenants": 0, "users": 0}', out.getvalue())

    def test_destroy_fails_closed_if_same_code_is_recreated_before_readback(self):
        tenant_code = "qa-ymath-realuse-destroy-race"
        self._call_command(tenant_code=tenant_code)
        original = Command._remaining_for_code

        def recreate_and_readback(code):
            Tenant.objects.create(code=code, name="raced replacement")
            return original(code)

        with patch.object(Command, "_remaining_for_code", side_effect=recreate_and_readback):
            with self.assertRaisesMessage(CommandError, "same-code"):
                with patch.dict(os.environ, {}, clear=True):
                    call_command(
                        "setup_ymath_realuse_scenario",
                        tenant_code=tenant_code,
                        destroy=True,
                    )

        self.assertEqual(Tenant.objects.filter(code=tenant_code).count(), 1)

    @override_settings(
        DATABASES={"default": {"NAME": "academy_api", "ENGINE": "django.db.backends.postgresql"}},
        R2_AI_BUCKET="academy-ai",
        R2_STORAGE_BUCKET="academy-storage",
        R2_EXCEL_BUCKET="academy-excel",
        R2_ADMIN_BUCKET="academy-admin",
    )
    def test_rejects_production_shaped_runtime(self):
        with self.assertRaisesMessage(CommandError, "isolated development"):
            self._call_command(login_uat=True)
