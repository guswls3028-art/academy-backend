# 출신중학교 (고등학생 선택) — Student + StudentRegistrationRequest

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("students", "0003_add_student_address"),
    ]

    operations = [
        migrations.AddField(
            model_name="student",
            name="origin_middle_school",
            field=models.CharField(
                blank=True,
                help_text="출신중학교 (고등학생 선택 입력)",
                max_length=100,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="studentregistrationrequest",
            name="origin_middle_school",
            field=models.CharField(
                blank=True,
                help_text="출신중학교 (고등학생 선택 입력)",
                max_length=100,
                null=True,
            ),
        ),
    ]
