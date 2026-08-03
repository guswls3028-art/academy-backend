from django.db import migrations


LAYOUT_KEY = "student_grade_report_layout"
YMATH_METRICS = {
    "average_score": True,
    "pass_rate": False,
    "status": False,
}


def apply_ymath_metrics(apps, schema_editor):
    Program = apps.get_model("core", "Program")
    for program in Program.objects.filter(tenant__code="ymath"):
        ui_config = dict(program.ui_config) if isinstance(program.ui_config, dict) else {}
        layout = dict(ui_config.get(LAYOUT_KEY)) if isinstance(ui_config.get(LAYOUT_KEY), dict) else {}
        layout.setdefault("score_comparison_metrics", YMATH_METRICS)
        layout["version"] = 2
        ui_config[LAYOUT_KEY] = layout
        program.ui_config = ui_config
        program.save(update_fields=["ui_config"])


def remove_ymath_metrics(apps, schema_editor):
    Program = apps.get_model("core", "Program")
    for program in Program.objects.filter(tenant__code="ymath"):
        ui_config = dict(program.ui_config) if isinstance(program.ui_config, dict) else {}
        stored = ui_config.get(LAYOUT_KEY)
        if not isinstance(stored, dict):
            continue
        layout = dict(stored)
        if layout.get("score_comparison_metrics") == YMATH_METRICS:
            layout.pop("score_comparison_metrics", None)
            if layout.get("version") == 2:
                layout["version"] = 1
            ui_config[LAYOUT_KEY] = layout
            program.ui_config = ui_config
            program.save(update_fields=["ui_config"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0053_set_ymath_student_grade_report_layout"),
    ]

    operations = [
        migrations.RunPython(apply_ymath_metrics, remove_ymath_metrics),
    ]
