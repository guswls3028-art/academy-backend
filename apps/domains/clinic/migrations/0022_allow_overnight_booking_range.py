from django.db import migrations, models


ACADEMY_MIGRATION_PHASE = "contract"
ACADEMY_MIGRATION_REASON = (
    "Reader-first 플래그를 유지하며 자정을 넘는 서로 다른 시작/종료 시각을 허용하도록 제약만 확장한다."
)


def set_constraint_ddl_timeouts(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            cursor.execute("SET LOCAL statement_timeout = '30s'")


class Migration(migrations.Migration):
    atomic = True
    dependencies = [("clinic", "0021_allow_booking_range_to_end_at_midnight")]
    operations = [
        migrations.RunPython(set_constraint_ddl_timeouts, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="sessionparticipant", name="clinic_participant_booking_range_order",
        ),
        migrations.AddConstraint(
            model_name="sessionparticipant",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(booking_start_time__isnull=True)
                    | ~models.Q(booking_start_time=models.F("booking_end_time"))
                ),
                name="clinic_participant_booking_range_order",
            ),
        ),
        migrations.RunPython(migrations.RunPython.noop, set_constraint_ddl_timeouts),
    ]
