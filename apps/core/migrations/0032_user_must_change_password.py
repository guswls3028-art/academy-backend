from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0031_remove_subscription_cancelled_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="must_change_password",
            field=models.BooleanField(
                default=False,
                help_text="True이면 로그인 후 비밀번호 변경 강제. 신규 학부모 계정 생성 시 설정.",
            ),
        ),
    ]
