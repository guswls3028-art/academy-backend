from django.db import migrations, models


def assert_no_open_work_record_duplicates(apps, schema_editor):
    table = schema_editor.quote_name("staffs_workrecord")
    with schema_editor.connection.cursor() as cursor:
        if schema_editor.connection.vendor == "postgresql":
            cursor.execute(f"LOCK TABLE {table} IN SHARE ROW EXCLUSIVE MODE")
        cursor.execute(
            f"""
            SELECT tenant_id, staff_id, COUNT(*)
            FROM {table}
            WHERE end_time IS NULL
            GROUP BY tenant_id, staff_id
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
        duplicate = cursor.fetchone()
    if duplicate:
        raise RuntimeError(
            "open WorkRecord duplicates remain; repair them before applying "
            "staffs.0007_unique_open_work_record"
        )


class Migration(migrations.Migration):

    dependencies = [
        ("staffs", "0006_workrecord_auto_calc_work_hours"),
    ]

    operations = [
        migrations.RunPython(
            assert_no_open_work_record_duplicates,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="workrecord",
            constraint=models.UniqueConstraint(
                condition=models.Q(end_time__isnull=True),
                fields=("tenant", "staff"),
                name="uniq_open_work_record_per_tenant_staff",
            ),
        ),
    ]
