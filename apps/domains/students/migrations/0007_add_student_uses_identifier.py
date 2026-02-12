# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("students", "0006_add_student_deleted_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="student",
            name="uses_identifier",
            field=models.BooleanField(
                default=False,
                help_text="True면 학생 전화 없음, 식별자(010+8자리)로 가입. 표시 시 '식별자 XXXX-XXXX'",
            ),
        ),
    ]
