"""Enable Ymath regular clinic mode and anonymous score billboard output."""

from django.db import migrations


YMATH_FEATURE_FLAGS = {
    "section_mode": True,
    "clinic_mode": "regular",
    "score_output_mode": "anonymous_billboard",
}


def apply_ymath_billboard_mode(apps, schema_editor):
    Program = apps.get_model("core", "Program")

    for program in Program.objects.filter(tenant__code="ymath"):
        feature_flags = dict(program.feature_flags or {})
        feature_flags.update(YMATH_FEATURE_FLAGS)
        program.feature_flags = feature_flags
        program.save(update_fields=["feature_flags"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0041_set_ymath_limglish_contract_price"),
    ]

    operations = [
        migrations.RunPython(apply_ymath_billboard_mode, migrations.RunPython.noop),
    ]
