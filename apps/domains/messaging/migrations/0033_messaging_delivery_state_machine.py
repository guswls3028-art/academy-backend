from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("messaging", "0032_notificationlog_source_tenant_fk"),
    ]

    operations = [
        migrations.AlterField(
            model_name="notificationlog",
            name="status",
            field=models.CharField(
                blank=True,
                choices=[
                    ("processing", "처리중"),
                    ("sending", "공급사 호출중"),
                    ("sent", "발송완료"),
                    ("retryable_failed", "재시도 대기"),
                    ("failed", "실패"),
                    ("ambiguous", "결과 확인 필요"),
                ],
                default="sent",
                help_text=(
                    "발송 상태. processing=공급사 호출 전 선점, sending=공급사 호출 시작, "
                    "sent=발송 접수 완료, retryable_failed=공급사 호출 전 재시도 가능 실패, "
                    "failed=확정 실패, ambiguous=공급사 호출 결과 수동 확인 필요"
                ),
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="schedulednotification",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "대기"),
                    ("dispatching", "큐 등록 중"),
                    ("sent", "큐 등록 완료"),
                    ("failed", "실패"),
                    ("cancelled", "취소"),
                ],
                db_index=True,
                default="pending",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="schedulednotification",
            name="attempt_count",
            field=models.PositiveSmallIntegerField(db_default=0, default=0),
        ),
        migrations.AddField(
            model_name="schedulednotification",
            name="dispatch_key",
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.AddField(
            model_name="schedulednotification",
            name="business_idempotency_key",
            field=models.CharField(
                blank=True,
                db_default="",
                db_index=True,
                default="",
                help_text="enqueue 시 NotificationLog와 공유하는 SHA-256 business key",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="schedulednotification",
            name="last_attempt_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="schedulednotification",
            name="next_attempt_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AlterField(
            model_name="schedulednotification",
            name="sent_at",
            field=models.DateTimeField(
                blank=True,
                help_text="SQS가 발송 작업을 접수한 시각(공급사 최종 발송 시각이 아님)",
                null=True,
            ),
        ),
    ]
