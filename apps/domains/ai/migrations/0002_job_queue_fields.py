from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ai_domain", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="aijobmodel",
            name="retry_count",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="aijobmodel",
            name="max_retries",
            field=models.IntegerField(default=5),
        ),
        migrations.AddField(
            model_name="aijobmodel",
            name="locked_by",
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name="aijobmodel",
            name="locked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
