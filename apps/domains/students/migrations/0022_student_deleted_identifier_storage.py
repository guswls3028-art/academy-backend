from django.db import migrations, models

ACADEMY_MIGRATION_PHASE = "expand"
ACADEMY_MIGRATION_REASON = "최대 50자 학생 아이디를 bigint 삭제 접두사와 함께 손실 없이 보존한다. 일반 입력은 50자를 유지한다."


class Migration(migrations.Migration):
    dependencies = [("students", "0021_registration_parent_password_choice")]
    operations = [
        migrations.AlterField(
            model_name="student", name="ps_number",
            field=models.CharField(max_length=75, help_text="PS 번호 (학원 학생 ID)"),
        ),
        # Expand-only: image rollback keeps this schema. Never truncate original IDs.
        migrations.RunPython(migrations.RunPython.noop),
    ]
