from django.db import migrations, models

ACADEMY_MIGRATION_PHASE = "expand"
ACADEMY_MIGRATION_REASON = "학생 소프트 삭제의 원본 아이디와 bigint 접두사를 인벤토리 소유권 metadata에도 손실 없이 보존한다. R2 key는 변경하지 않는다."


class Migration(migrations.Migration):
    dependencies = [("inventory", "0002_alter_inventoryfile_student_ps_and_more")]
    operations = [
        migrations.AlterField(model_name="inventoryfolder", name="student_ps",
            field=models.CharField(blank=True, db_index=True, default="", max_length=75)),
        migrations.AlterField(model_name="inventoryfile", name="student_ps",
            field=models.CharField(blank=True, db_index=True, default="", max_length=75)),
        # Expand-only: image rollback keeps this schema. Never truncate ownership IDs.
        migrations.RunPython(migrations.RunPython.noop),
    ]
