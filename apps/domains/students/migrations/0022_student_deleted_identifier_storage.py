from django.db import migrations, models
from django.db.migrations.exceptions import IrreversibleError
from django.db.models.functions import Length

ACADEMY_MIGRATION_PHASE = "expand"
ACADEMY_MIGRATION_REASON = "최대 50자 학생 아이디를 bigint 삭제 접두사와 함께 손실 없이 보존한다. 일반 입력은 50자를 유지한다."


def guard_identifier_shrink(apps, schema_editor):
    connection = schema_editor.connection
    if not schema_editor.atomic_migration or not connection.in_atomic_block:
        raise IrreversibleError("Identifier shrink requires one atomic migration transaction.")
    student = apps.get_model("students", "Student")
    if connection.vendor == "postgresql":
        table = connection.ops.quote_name(student._meta.db_table)
        with connection.cursor() as cursor:
            cursor.execute(f"LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE")
    if student.objects.using(connection.alias).annotate(
        identifier_length=Length("ps_number")
    ).filter(identifier_length__gt=50).exists():
        raise IrreversibleError("Keep expanded storage while student identifiers exceed 50 characters.")


class Migration(migrations.Migration):
    atomic = True
    dependencies = [("students", "0021_registration_parent_password_choice")]
    operations = [
        migrations.AlterField(
            model_name="student", name="ps_number",
            field=models.CharField(max_length=75, help_text="PS 번호 (학원 학생 ID)"),
        ),
        # Reverse locks survive the scan through subsequent narrowing DDL.
        migrations.RunPython(migrations.RunPython.noop, guard_identifier_shrink),
    ]
