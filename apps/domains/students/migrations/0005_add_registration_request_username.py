# Generated manually: StudentRegistrationRequest 희망 로그인 아이디 (선택)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("students", "0004_add_origin_middle_school"),
    ]

    operations = [
        migrations.AddField(
            model_name="studentregistrationrequest",
            name="username",
            field=models.CharField(
                max_length=50,
                blank=True,
                default="",
                help_text="희망 로그인 아이디 (비어 있으면 승인 시 자동 부여)",
            ),
        ),
    ]
