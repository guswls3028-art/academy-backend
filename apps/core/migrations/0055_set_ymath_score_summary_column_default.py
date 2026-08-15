"""Default Ymath's score-table summary to exam correction status."""

from django.db import migrations


FLAG_KEY = "score_summary_column_default"
YMATH_DEFAULT = "exam_wrong"


def apply_ymath_score_summary_default(apps, schema_editor):
    Program = apps.get_model("core", "Program")
    for program in Program.objects.filter(tenant__code="ymath"):
        feature_flags = dict(program.feature_flags or {})
        feature_flags.setdefault(FLAG_KEY, YMATH_DEFAULT)
        program.feature_flags = feature_flags
        program.save(update_fields=["feature_flags"])


def remove_seeded_ymath_score_summary_default(apps, schema_editor):
    Program = apps.get_model("core", "Program")
    for program in Program.objects.filter(tenant__code="ymath"):
        feature_flags = dict(program.feature_flags or {})
        if feature_flags.get(FLAG_KEY) == YMATH_DEFAULT:
            feature_flags.pop(FLAG_KEY, None)
            program.feature_flags = feature_flags
            program.save(update_fields=["feature_flags"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0054_ymath_grade_report_comparison_metrics"),
    ]

    operations = [
        migrations.RunPython(
            apply_ymath_score_summary_default,
            remove_seeded_ymath_score_summary_default,
        ),
    ]
