# Phase 0: Job 상태 머신 확장 (설계 4.1 반영)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ai_domain", "0007_idempotency_tenant_runtime_config"),
    ]

    operations = [
        migrations.AlterField(
            model_name="aijobmodel",
            name="status",
            field=models.CharField(
                choices=[
                    ("PENDING", "PENDING"),
                    ("VALIDATING", "VALIDATING"),
                    ("RUNNING", "RUNNING"),
                    ("DONE", "DONE"),
                    ("FAILED", "FAILED"),
                    ("REJECTED_BAD_INPUT", "REJECTED_BAD_INPUT"),
                    ("FALLBACK_TO_GPU", "FALLBACK_TO_GPU"),
                    ("RETRYING", "RETRYING"),
                    ("REVIEW_REQUIRED", "REVIEW_REQUIRED"),
                ],
                db_index=True,
                default="PENDING",
                max_length=32,
            ),
        ),
    ]
