import importlib
from io import StringIO

from django.apps import apps as django_apps
from django.core.management import call_command
from django.test import TestCase

from apps.core.models import Program, Tenant
from apps.core.services.student_grade_report_layout import (
    STUDENT_GRADE_REPORT_LAYOUT_KEY,
)


class SetupThreeTenantsTests(TestCase):
    def _call_command(self):
        out = StringIO()
        call_command("setup_three_tenants", stdout=out)
        return out.getvalue()

    def test_ymath_gets_anonymous_billboard_flags(self):
        self._call_command()

        ymath_program = Program.objects.get(tenant__code="ymath")
        self.assertFalse(ymath_program.feature_flags["section_mode"])
        self.assertEqual(ymath_program.feature_flags["clinic_mode"], "remediation")
        self.assertEqual(
            ymath_program.feature_flags["score_output_mode"],
            "anonymous_billboard",
        )
        self.assertEqual(
            ymath_program.feature_flags["score_summary_column_default"],
            "exam_wrong",
        )
        self.assertEqual(
            ymath_program.feature_flags["assessment_status_display"],
            "wrong_completion",
        )
        visible = {
            row["id"]: row["visible"]
            for row in ymath_program.ui_config[STUDENT_GRADE_REPORT_LAYOUT_KEY]["sections"]
        }
        self.assertTrue(visible["lecture_average"])
        self.assertFalse(visible["improvement_priority"])
        self.assertFalse(visible["exam_summary"])
        metrics = ymath_program.ui_config[STUDENT_GRADE_REPORT_LAYOUT_KEY]["score_comparison_metrics"]
        self.assertTrue(metrics["average_score"])
        self.assertFalse(metrics["pass_rate"])
        self.assertFalse(metrics["status"])

        tchul_program = Program.objects.get(tenant__code="tchul")
        self.assertNotIn("score_output_mode", tchul_program.feature_flags)
        self.assertNotIn("score_summary_column_default", tchul_program.feature_flags)
        self.assertNotIn("assessment_status_display", tchul_program.feature_flags)

    def test_existing_ymath_flags_are_repaired_without_dropping_custom_flags(self):
        tenant = Tenant.objects.create(code="ymath", name="Ymath", is_active=True)
        program = Program.objects.get(tenant=tenant)
        program.feature_flags = {
            "custom_flag": "keep",
            "section_mode": True,
            "clinic_mode": "regular",
        }
        program.save(update_fields=["feature_flags"])

        self._call_command()

        program.refresh_from_db()
        self.assertEqual(program.feature_flags["custom_flag"], "keep")
        self.assertFalse(program.feature_flags["section_mode"])
        self.assertEqual(program.feature_flags["clinic_mode"], "remediation")
        self.assertEqual(
            program.feature_flags["score_output_mode"],
            "anonymous_billboard",
        )
        self.assertEqual(
            program.feature_flags["score_summary_column_default"],
            "exam_wrong",
        )
        self.assertEqual(
            program.feature_flags["assessment_status_display"],
            "wrong_completion",
        )
        self.assertIn(STUDENT_GRADE_REPORT_LAYOUT_KEY, program.ui_config)

    def test_existing_ymath_non_object_ui_config_is_repaired(self):
        tenant = Tenant.objects.create(code="ymath", name="Ymath", is_active=True)
        program = Program.objects.get(tenant=tenant)
        program.ui_config = ["broken"]
        program.save(update_fields=["ui_config"])

        self._call_command()

        program.refresh_from_db()
        self.assertEqual(
            program.ui_config[STUDENT_GRADE_REPORT_LAYOUT_KEY]["version"],
            2,
        )

    def test_score_summary_default_migration_is_ymath_only_and_preserves_flags(self):
        migration = importlib.import_module(
            "apps.core.migrations.0055_set_ymath_score_summary_column_default"
        )
        ymath = Tenant.objects.create(code="ymath", name="Ymath", is_active=True)
        ymath_program = Program.objects.get(tenant=ymath)
        ymath_program.feature_flags = {"custom_flag": "keep"}
        ymath_program.save(update_fields=["feature_flags"])
        other = Tenant.objects.create(code="tchul", name="Tchul", is_active=True)
        other_program = Program.objects.get(tenant=other)
        other_program.feature_flags = {"custom_flag": "other"}
        other_program.save(update_fields=["feature_flags"])

        migration.apply_ymath_score_summary_default(django_apps, None)

        ymath_program.refresh_from_db()
        other_program.refresh_from_db()
        self.assertEqual(ymath_program.feature_flags["custom_flag"], "keep")
        self.assertEqual(
            ymath_program.feature_flags["score_summary_column_default"],
            "exam_wrong",
        )
        self.assertEqual(other_program.feature_flags, {"custom_flag": "other"})

        migration.remove_seeded_ymath_score_summary_default(django_apps, None)

        ymath_program.refresh_from_db()
        self.assertEqual(ymath_program.feature_flags, {"custom_flag": "keep"})

    def test_assessment_status_display_migration_is_ymath_only_and_preserves_flags(self):
        migration = importlib.import_module(
            "apps.core.migrations.0060_set_ymath_wrong_completion_display"
        )
        ymath = Tenant.objects.create(code="ymath", name="Ymath", is_active=True)
        ymath_program = Program.objects.get(tenant=ymath)
        ymath_program.feature_flags = {"custom_flag": "keep"}
        ymath_program.save(update_fields=["feature_flags"])
        other = Tenant.objects.create(code="tchul", name="Tchul", is_active=True)
        other_program = Program.objects.get(tenant=other)
        other_program.feature_flags = {"custom_flag": "other"}
        other_program.save(update_fields=["feature_flags"])

        migration.apply_ymath_wrong_completion_display(django_apps, None)

        ymath_program.refresh_from_db()
        other_program.refresh_from_db()
        self.assertEqual(ymath_program.feature_flags["custom_flag"], "keep")
        self.assertEqual(
            ymath_program.feature_flags["assessment_status_display"],
            "wrong_completion",
        )
        self.assertEqual(other_program.feature_flags, {"custom_flag": "other"})

        migration.remove_seeded_ymath_wrong_completion_display(django_apps, None)

        ymath_program.refresh_from_db()
        self.assertEqual(ymath_program.feature_flags, {"custom_flag": "keep"})
