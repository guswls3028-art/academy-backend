"""Seed YMath's editable student growth-report layout preference."""

from django.db import migrations


LAYOUT_KEY = "student_grade_report_layout"
YMATH_LAYOUT = {
    "version": 1,
    "sections": [
        {"id": "score_trend", "visible": True},
        {"id": "score_comparison", "visible": True},
        {"id": "lecture_average", "visible": True},
        {"id": "improvement_priority", "visible": False},
        {"id": "exam_summary", "visible": False},
        {"id": "rank_position", "visible": False},
        {"id": "weakest_lecture", "visible": False},
        {"id": "homework_summary", "visible": False},
    ],
}


def apply_ymath_layout(apps, schema_editor):
    Program = apps.get_model("core", "Program")
    for program in Program.objects.filter(tenant__code="ymath"):
        ui_config = dict(program.ui_config) if isinstance(program.ui_config, dict) else {}
        ui_config.setdefault(LAYOUT_KEY, YMATH_LAYOUT)
        program.ui_config = ui_config
        program.save(update_fields=["ui_config"])


def remove_seeded_ymath_layout(apps, schema_editor):
    Program = apps.get_model("core", "Program")
    for program in Program.objects.filter(tenant__code="ymath"):
        ui_config = dict(program.ui_config) if isinstance(program.ui_config, dict) else {}
        if ui_config.get(LAYOUT_KEY) == YMATH_LAYOUT:
            ui_config.pop(LAYOUT_KEY, None)
            program.ui_config = ui_config
            program.save(update_fields=["ui_config"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0052_user_first_login_guide_completed_at"),
    ]

    operations = [
        migrations.RunPython(apply_ymath_layout, remove_seeded_ymath_layout),
    ]
