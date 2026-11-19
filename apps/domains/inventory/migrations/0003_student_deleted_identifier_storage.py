from django.db import migrations, models
from django.db.migrations.exceptions import IrreversibleError
from django.db.models.functions import Length

ACADEMY_MIGRATION_PHASE = "expand"
ACADEMY_MIGRATION_REASON = "학생 소프트 삭제의 원본 아이디와 bigint 접두사를 인벤토리 소유권 metadata에도 손실 없이 보존한다. R2 key는 변경하지 않는다."


def guard_identifier_shrink(apps, schema_editor):
    connection = schema_editor.connection
    if not schema_editor.atomic_migration or not connection.in_atomic_block:
        raise IrreversibleError("Identifier shrink requires one atomic migration transaction.")
    targets = [apps.get_model("inventory", name) for name in ("InventoryFile", "InventoryFolder")]
    targets.sort(key=lambda model: model._meta.db_table)
    if connection.vendor == "postgresql":
        tables = ", ".join(connection.ops.quote_name(model._meta.db_table) for model in targets)
        with connection.cursor() as cursor:
            cursor.execute(f"LOCK TABLE {tables} IN ACCESS EXCLUSIVE MODE")
    for model in targets:
        if model.objects.using(connection.alias).annotate(
            identifier_length=Length("student_ps")
        ).filter(identifier_length__gt=50).exists():
            raise IrreversibleError("Keep expanded storage while inventory identifiers exceed 50 characters.")


class Migration(migrations.Migration):
    atomic = True
    dependencies = [("inventory", "0002_alter_inventoryfile_student_ps_and_more")]
    operations = [
        migrations.AlterField(model_name="inventoryfolder", name="student_ps",
            field=models.CharField(blank=True, db_index=True, default="", max_length=75)),
        migrations.AlterField(model_name="inventoryfile", name="student_ps",
            field=models.CharField(blank=True, db_index=True, default="", max_length=75)),
        # Lock both tables before either scan; retain locks through both ALTERs.
        migrations.RunPython(migrations.RunPython.noop, guard_identifier_shrink),
    ]
