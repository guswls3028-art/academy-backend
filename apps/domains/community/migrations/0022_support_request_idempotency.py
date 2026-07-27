from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("community", "0021_postentity_support_kind"),
    ]

    operations = [
        migrations.AddField(
            model_name="postentity",
            name="support_request_key",
            field=models.CharField(
                blank=True,
                help_text="비공개 지원 티켓 생성 재시도 식별자.",
                max_length=80,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="postreply",
            name="platform_request_key",
            field=models.CharField(
                blank=True,
                help_text="플랫폼 답변 생성 재시도 식별자.",
                max_length=80,
                null=True,
            ),
        ),
        migrations.AddConstraint(
            model_name="postentity",
            constraint=models.UniqueConstraint(
                condition=models.Q(("support_request_key__isnull", False)),
                fields=("tenant", "support_request_key"),
                name="comm_post_support_req_uq",
            ),
        ),
        migrations.AddConstraint(
            model_name="postreply",
            constraint=models.UniqueConstraint(
                condition=models.Q(("platform_request_key__isnull", False)),
                fields=("post", "platform_request_key"),
                name="comm_reply_platform_req_uq",
            ),
        ),
    ]
