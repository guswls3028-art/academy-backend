# OpsEvent model for video encoding observability

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("video", "0006_unique_video_active_job"),
    ]

    operations = [
        migrations.CreateModel(
            name="VideoOpsEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("type", models.CharField(db_index=True, max_length=64)),
                ("severity", models.CharField(db_index=True, default="WARNING", max_length=16)),
                ("tenant_id", models.PositiveIntegerField(blank=True, db_index=True, null=True)),
                ("video_id", models.PositiveIntegerField(blank=True, db_index=True, null=True)),
                ("job_id", models.UUIDField(blank=True, db_index=True, null=True)),
                ("aws_batch_job_id", models.CharField(blank=True, db_index=True, max_length=256)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="videoopsevent",
            index=models.Index(fields=["type", "created_at"], name="video_videoo_type_0a1b0d_idx"),
        ),
    ]
