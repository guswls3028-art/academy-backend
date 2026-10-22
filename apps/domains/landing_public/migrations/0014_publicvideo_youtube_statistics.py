from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("landing_public", "0013_neutral_launch_video_copy")]

    operations = [
        migrations.AddField(model_name="publicvideo", name="youtube_view_count", field=models.PositiveBigIntegerField(blank=True, null=True)),
        migrations.AddField(model_name="publicvideo", name="youtube_published_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="publicvideo", name="youtube_statistics_checked_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="publicvideo", name="youtube_statistics_retry_at", field=models.DateTimeField(blank=True, null=True)),
    ]
