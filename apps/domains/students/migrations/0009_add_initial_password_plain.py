"""
Add initial_password_plain field to StudentRegistrationRequest.
Stores plaintext password temporarily for alimtalk notification on approval.
Cleared immediately after approval + notification.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("students", "0008_tag_tenant_isolation"),
    ]

    operations = [
        migrations.AddField(
            model_name="studentregistrationrequest",
            name="initial_password_plain",
            field=models.CharField(
                blank=True,
                default="",
                help_text="알림톡 발송용 원문 비밀번호. 승인 후 즉시 삭제.",
                max_length=128,
            ),
        ),
    ]
