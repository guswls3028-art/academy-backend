from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0046_apply_single_subscription_plan"),
    ]

    operations = [
        migrations.AddField(
            model_name="landingconsultrequest",
            name="privacy_agreed",
            field=models.BooleanField(
                default=False,
                help_text="개인정보 수집·이용에 명시적으로 동의했는지 여부",
            ),
        ),
        migrations.AddField(
            model_name="landingconsultrequest",
            name="privacy_agreed_at",
            field=models.DateTimeField(
                blank=True,
                help_text="서버가 기록한 개인정보 수집·이용 동의 시각",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="landingconsultrequest",
            name="privacy_policy_version",
            field=models.CharField(
                blank=True,
                default="",
                help_text="동의 시점의 개인정보처리방침 버전",
                max_length=20,
            ),
        ),
    ]
