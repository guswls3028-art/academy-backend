# Generated for Result.objective_score (객관식/주관식/합산 동기화)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("results", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="result",
            name="objective_score",
            field=models.FloatField(default=0.0),
        ),
    ]
