"""Keep Ymath anonymous scoreboard without section-mode class assignment."""

from django.db import migrations


YMATH_NO_SECTION_FLAGS = {
    "section_mode": False,
    "clinic_mode": "remediation",
    "score_output_mode": "anonymous_billboard",
}

YMATH_REGULAR_SECTION_FLAGS = {
    "section_mode": True,
    "clinic_mode": "regular",
    "score_output_mode": "anonymous_billboard",
}


def apply_ymath_no_section_mode(apps, schema_editor):
    Program = apps.get_model("core", "Program")

    for program in Program.objects.filter(tenant__code="ymath"):
        feature_flags = dict(program.feature_flags or {})
        feature_flags.update(YMATH_NO_SECTION_FLAGS)
        program.feature_flags = feature_flags
        program.save(update_fields=["feature_flags"])


def restore_ymath_regular_section_mode(apps, schema_editor):
    Program = apps.get_model("core", "Program")

    for program in Program.objects.filter(tenant__code="ymath"):
        feature_flags = dict(program.feature_flags or {})
        feature_flags.update(YMATH_REGULAR_SECTION_FLAGS)
        program.feature_flags = feature_flags
        program.save(update_fields=["feature_flags"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0042_set_ymath_regular_clinic_billboard"),
    ]

    operations = [
        migrations.RunPython(
            apply_ymath_no_section_mode,
            restore_ymath_regular_section_mode,
        ),
    ]
