# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("students", "0005_alter_student_omr_code_alter_student_ps_number"),
    ]

    operations = [
        migrations.AddField(
            model_name="student",
            name="deleted_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text="삭제일시. 설정 시 30일 보관 후 자동 삭제",
                null=True,
            ),
        ),
    ]
