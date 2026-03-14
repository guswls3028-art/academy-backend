"""Add business_idempotency_key, status, claimed_at to NotificationLog for atomic claim dedup."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("messaging", "0006_notificationlog_sqs_message_id"),
    ]
    operations = [
        migrations.AddField(
            model_name="notificationlog",
            name="business_idempotency_key",
            field=models.CharField(blank=True, default="", max_length=64,
                help_text="SHA-256 hash of business dedup key. Empty for legacy."),
        ),
        migrations.AddField(
            model_name="notificationlog",
            name="status",
            field=models.CharField(blank=True, default="sent", max_length=20,
                choices=[("processing", "처리중"), ("sent", "발송완료"), ("failed", "실패")],
                help_text="발송 상태"),
        ),
        migrations.AddField(
            model_name="notificationlog",
            name="claimed_at",
            field=models.DateTimeField(blank=True, null=True, help_text="Worker 선점 시각"),
        ),
        migrations.AddConstraint(
            model_name="notificationlog",
            constraint=models.UniqueConstraint(
                condition=models.Q(business_idempotency_key__gt=""),
                fields=["tenant", "message_mode", "business_idempotency_key"],
                name="uniq_notification_business_key_per_tenant_channel",
            ),
        ),
    ]
