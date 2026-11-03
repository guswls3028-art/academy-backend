from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("video", "0024_review_watch_usage")]

    operations = [
        migrations.AddField(
            model_name="videoreviewwatchstream", name="prepared_position_ms",
            field=models.PositiveBigIntegerField(null=True),
        ),
        migrations.AddField(
            model_name="videoreviewwatchstream", name="prepared_until",
            field=models.DateTimeField(null=True),
        ),
    ]
