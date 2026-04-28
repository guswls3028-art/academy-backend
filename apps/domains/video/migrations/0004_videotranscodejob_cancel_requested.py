# Generated manually for ENTERPRISE STABILIZATION PATCH

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("video", "0003_videotranscodejob_video_current_job"),
    ]

    operations = [
        migrations.AddField(
            model_name="videotranscodejob",
            name="cancel_requested",
            field=models.BooleanField(default=False),
        ),
    ]
