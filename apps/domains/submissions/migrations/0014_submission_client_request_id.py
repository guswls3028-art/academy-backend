from django.db import migrations, models


class Migration(migrations.Migration):
    # Commit the nullable expansion and its recorder row together, independently
    # of the non-atomic concurrent index build in 0015.
    dependencies = [("submissions", "0013_homework_media_preview")]

    operations = [
        migrations.AddField(
            model_name="submission",
            name="client_request_id",
            field=models.UUIDField(blank=True, editable=False, null=True),
        ),
    ]
