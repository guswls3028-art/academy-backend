from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("community", "0020_communitynotification"),
    ]

    operations = [
        migrations.AddField(
            model_name="postentity",
            name="support_kind",
            field=models.CharField(
                blank=True,
                choices=[("bug", "버그"), ("feedback", "피드백")],
                db_index=True,
                help_text="개발자 비공개 지원 티켓 분류. null이면 일반 커뮤니티 글.",
                max_length=20,
                null=True,
            ),
        ),
    ]
