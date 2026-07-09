from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.core.models import Program, Tenant


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

        tchul_program = Program.objects.get(tenant__code="tchul")
        self.assertNotIn("score_output_mode", tchul_program.feature_flags)

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
