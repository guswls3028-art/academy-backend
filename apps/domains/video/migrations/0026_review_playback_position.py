from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("video", "0025_review_prefetch_window")]

    operations = [
        migrations.AddField(
            model_name="videoreviewwatchstream", name="playback_position_ms",
            field=models.PositiveBigIntegerField(null=True),
        ),
    ]
