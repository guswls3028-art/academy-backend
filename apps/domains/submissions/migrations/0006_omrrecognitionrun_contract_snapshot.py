from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("submissions", "0005_omrrecognitionrun_omrdetectedanswer_omrstudentmatch_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="omrrecognitionrun",
            name="contract_snapshot",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
