"""
Invoice: provider_order_id, failed_at, failure_reason, attempt_count, next_retry_at 추가.
PaymentTransaction: provider_payment_key, provider_order_id, idempotency_key,
                    request_payload, response_payload, reconciled_at 추가.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0002_invoice_unique_invoice_per_period"),
    ]

    operations = [
        # ── Invoice 필드 추가 ──
        migrations.AddField(
            model_name="invoice",
            name="provider_order_id",
            field=models.CharField(
                blank=True, default="",
                help_text="PG 주문 식별용 고유 ID (UUID 기반, invoice_number와 분리)",
                max_length=64, unique=True,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="invoice",
            name="failed_at",
            field=models.DateTimeField(
                blank=True, null=True,
                help_text="마지막 결제 실패 시각",
            ),
        ),
        migrations.AddField(
            model_name="invoice",
            name="failure_reason",
            field=models.TextField(blank=True, help_text="마지막 결제 실패 사유"),
        ),
        migrations.AddField(
            model_name="invoice",
            name="attempt_count",
            field=models.PositiveSmallIntegerField(
                default=0, help_text="결제 시도 횟수",
            ),
        ),
        migrations.AddField(
            model_name="invoice",
            name="next_retry_at",
            field=models.DateField(
                blank=True, null=True,
                help_text="다음 재시도 예정일",
            ),
        ),

        # ── PaymentTransaction 필드 추가 ──
        migrations.AddField(
            model_name="paymenttransaction",
            name="provider_payment_key",
            field=models.CharField(
                blank=True, default="",
                help_text="PG사 결제 키 (Toss: paymentKey)",
                max_length=200,
            ),
        ),
        migrations.AddField(
            model_name="paymenttransaction",
            name="provider_order_id",
            field=models.CharField(
                blank=True, default="",
                help_text="PG 주문 ID (Invoice.provider_order_id와 동일)",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="paymenttransaction",
            name="idempotency_key",
            field=models.CharField(
                blank=True, null=True, unique=True,
                help_text="멱등성 키 — 동일 결제 중복 방지",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="paymenttransaction",
            name="request_payload",
            field=models.JSONField(default=dict, help_text="PG 요청 페이로드"),
        ),
        migrations.AddField(
            model_name="paymenttransaction",
            name="response_payload",
            field=models.JSONField(default=dict, help_text="PG 응답 페이로드 (원본)"),
        ),
        migrations.AddField(
            model_name="paymenttransaction",
            name="reconciled_at",
            field=models.DateTimeField(
                blank=True, null=True,
                help_text="대사 완료 시각",
            ),
        ),
    ]
