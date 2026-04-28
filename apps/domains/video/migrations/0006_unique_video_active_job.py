# Phase 1: Single active job per video

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("video", "0005_videotranscodejob_aws_batch_job_id"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="videotranscodejob",
            constraint=models.UniqueConstraint(
                condition=models.Q(state__in=["QUEUED", "RUNNING", "RETRY_WAIT"]),
                fields=("video",),
                name="unique_video_active_job",
            ),
        ),
    ]
