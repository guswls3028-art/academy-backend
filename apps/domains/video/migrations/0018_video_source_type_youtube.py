from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("video", "0017_videotranscodejob_last_counted_failure_aws_batch_job_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="video",
            name="source_type",
            field=models.CharField(
                choices=[("s3", "직접 업로드"), ("youtube", "YouTube 링크")],
                db_index=True,
                default="s3",
                help_text="영상 소스: s3(직접 업로드) / youtube(YouTube 링크)",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="video",
            name="youtube_url",
            field=models.URLField(
                blank=True,
                default="",
                help_text="Canonical YouTube watch URL. source_type=youtube일 때만 사용.",
                max_length=500,
            ),
        ),
        migrations.AddField(
            model_name="video",
            name="youtube_video_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text="YouTube video id. source_type=youtube일 때만 사용.",
                max_length=32,
            ),
        ),
    ]
