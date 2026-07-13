from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0006_alter_billingprofile_provider_customer_key_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="paymenttransaction",
            name="status",
            field=models.CharField(
                choices=[
                    ("PENDING", "처리 중"),
                    ("PROCESSING", "공급사 처리 중"),
                    ("SUCCESS", "성공"),
                    ("FAILED", "실패"),
                    ("REFUNDED", "환불"),
                    ("PARTIALLY_REFUNDED", "부분 환불"),
                ],
                default="PENDING",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="paymenttransaction",
            name="processing_started_at",
            field=models.DateTimeField(
                blank=True,
                help_text="중복 PG 호출 방지를 위해 공급사 호출권을 선점한 시각",
                null=True,
            ),
        ),
        migrations.AddIndex(
            model_name="paymenttransaction",
            index=models.Index(
                fields=["status", "processing_started_at"],
                name="billing_tx_status_started_idx",
            ),
        ),
    ]
