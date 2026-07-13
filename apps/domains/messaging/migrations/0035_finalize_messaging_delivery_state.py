from __future__ import annotations

import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("messaging", "0034_backfill_messaging_delivery_state"),
    ]

    operations = [
        migrations.AlterField(
            model_name="schedulednotification",
            name="dispatch_key",
            field=models.UUIDField(
                default=uuid.uuid4,
                editable=False,
                help_text="예약/즉시 발송 아웃박스 행의 안정적인 dispatch 식별자",
                null=True,
            ),
        ),
        migrations.AddConstraint(
            model_name="schedulednotification",
            constraint=models.UniqueConstraint(
                condition=models.Q(dispatch_key__isnull=False),
                fields=("dispatch_key",),
                name="uniq_sched_dispatch_key_not_null",
            ),
        ),
        migrations.AddIndex(
            model_name="schedulednotification",
            index=models.Index(
                fields=["status", "next_attempt_at"],
                name="idx_sched_notif_status_retry",
            ),
        ),
    ]
