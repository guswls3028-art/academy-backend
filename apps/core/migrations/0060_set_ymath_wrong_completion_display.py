"""Present Ymath assessment status as wrong-answer completion only."""

from django.db import migrations


FLAG_KEY = "assessment_status_display"
YMATH_DISPLAY = "wrong_completion"


def apply_ymath_wrong_completion_display(apps, schema_editor):
    Program = apps.get_model("core", "Program")
    for program in Program.objects.filter(tenant__code="ymath"):
        feature_flags = dict(program.feature_flags or {})
        feature_flags.setdefault(FLAG_KEY, YMATH_DISPLAY)
        program.feature_flags = feature_flags
        program.save(update_fields=["feature_flags"])


def remove_seeded_ymath_wrong_completion_display(apps, schema_editor):
    Program = apps.get_model("core", "Program")
    for program in Program.objects.filter(tenant__code="ymath"):
        feature_flags = dict(program.feature_flags or {})
        if feature_flags.get(FLAG_KEY) == YMATH_DISPLAY:
            feature_flags.pop(FLAG_KEY, None)
            program.feature_flags = feature_flags
            program.save(update_fields=["feature_flags"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0059_tenant_clinic_multi_slot_booking_default"),
    ]

    operations = [
        migrations.RunPython(
            apply_ymath_wrong_completion_display,
            remove_seeded_ymath_wrong_completion_display,
        ),
    ]
