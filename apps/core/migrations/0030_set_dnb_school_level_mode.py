"""
Data migration: Set school_level_mode=elementary_middle for DNB tenant (ID=9).
"""
from django.db import migrations


def set_dnb_school_level_mode(apps, schema_editor):
    Program = apps.get_model("core", "Program")
    try:
        program = Program.objects.get(tenant_id=9)
        flags = program.feature_flags or {}
        flags["school_level_mode"] = "elementary_middle"
        program.feature_flags = flags
        program.save(update_fields=["feature_flags"])
    except Program.DoesNotExist:
        pass  # DNB tenant not in this DB (e.g., dev env without tenant 9)


def revert(apps, schema_editor):
    Program = apps.get_model("core", "Program")
    try:
        program = Program.objects.get(tenant_id=9)
        flags = program.feature_flags or {}
        flags.pop("school_level_mode", None)
        program.feature_flags = flags
        program.save(update_fields=["feature_flags"])
    except Program.DoesNotExist:
        pass


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0029_add_is_system_is_user_default_to_template"),
    ]

    operations = [
        migrations.RunPython(set_dnb_school_level_mode, revert),
    ]
