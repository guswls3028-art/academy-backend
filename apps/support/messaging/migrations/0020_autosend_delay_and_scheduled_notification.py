"""
AutoSendConfig에 delay_mode/delay_value 추가 + ScheduledNotification 모델 생성.
영상 인코딩 완료 등 이벤트 후 지연/예약 발송 지원.
"""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
        ("messaging", "0019_backfill_alimtalk_default"),
    ]

    operations = [
        # AutoSendConfig — delay_mode, delay_value
        migrations.AddField(
            model_name="autosendconfig",
            name="delay_mode",
            field=models.CharField(
                max_length=20,
                choices=[
                    ("immediate", "즉시 발송"),
                    ("delay_minutes", "N분 후 발송"),
                    ("scheduled_hour", "지정 시각 발송"),
                ],
                default="immediate",
            ),
        ),
        migrations.AddField(
            model_name="autosendconfig",
            name="delay_value",
            field=models.PositiveIntegerField(null=True, blank=True),
        ),
        # ScheduledNotification
        migrations.CreateModel(
            name="ScheduledNotification",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("trigger", models.CharField(db_index=True, max_length=60)),
                ("send_at", models.DateTimeField(db_index=True)),
                ("payload", models.JSONField(help_text="enqueue_sms kwargs (to, text, message_mode, template_id, etc.)")),
                ("status", models.CharField(
                    choices=[("pending", "대기"), ("sent", "발송완료"), ("failed", "실패"), ("cancelled", "취소")],
                    db_index=True, default="pending", max_length=20,
                )),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("error_message", models.CharField(blank=True, default="", max_length=500)),
                ("tenant", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="scheduled_notifications",
                    to="core.tenant",
                )),
            ],
            options={
                "verbose_name": "Scheduled notification",
                "verbose_name_plural": "Scheduled notifications",
                "ordering": ["send_at"],
                "indexes": [
                    models.Index(fields=["status", "send_at"], name="idx_sched_notif_status_sendat"),
                ],
            },
        ),
    ]
