from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("video", "0014_alimtalk_default_and_video_trigger"),
    ]

    operations = [
        migrations.AddField(
            model_name="video",
            name="thumbnail_r2_key",
            field=models.CharField(
                blank=True,
                default="",
                max_length=500,
                help_text="R2 key of generated thumbnail (e.g. tenants/{tid}/video/hls/{vid}/thumbnail.jpg). Authoritative path written by worker; ImageField 'thumbnail' is legacy.",
            ),
        ),
    ]
