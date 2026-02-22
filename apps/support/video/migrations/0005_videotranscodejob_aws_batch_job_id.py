# Phase 2: Batch submit 추적/디버깅용

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("video", "0004_videotranscodejob_cancel_requested"),
    ]

    operations = [
        migrations.AddField(
            model_name="videotranscodejob",
            name="aws_batch_job_id",
            field=models.CharField(blank=True, db_index=True, max_length=256),
        ),
    ]
