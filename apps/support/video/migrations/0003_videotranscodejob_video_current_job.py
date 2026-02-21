# Generated manually for Job-based transcode pipeline

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("video", "0002_videofolder_video_folder_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="VideoTranscodeJob",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("tenant_id", models.PositiveIntegerField(db_index=True)),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("QUEUED", "대기"),
                            ("RUNNING", "실행중"),
                            ("SUCCEEDED", "완료"),
                            ("FAILED", "실패"),
                            ("RETRY_WAIT", "재시도대기"),
                            ("DEAD", "격리"),
                        ],
                        db_index=True,
                        default="QUEUED",
                        max_length=20,
                    ),
                ),
                ("attempt_count", models.PositiveIntegerField(default=1)),
                ("locked_by", models.CharField(blank=True, max_length=64)),
                ("locked_until", models.DateTimeField(blank=True, null=True)),
                ("last_heartbeat_at", models.DateTimeField(blank=True, null=True)),
                ("error_code", models.CharField(blank=True, max_length=64)),
                ("error_message", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "video",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="transcode_jobs",
                        to="video.video",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["state", "updated_at"],
                        name="video_transcodejob_state_updated_idx",
                    ),
                    models.Index(
                        fields=["tenant_id", "state"],
                        name="video_transcodejob_tenant_state_idx",
                    ),
                ],
            },
        ),
        migrations.AddField(
            model_name="video",
            name="current_job",
            field=models.ForeignKey(
                blank=True,
                help_text="현재 transcoding Job (진행 중 또는 최종)",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="video.videotranscodejob",
            ),
        ),
    ]
