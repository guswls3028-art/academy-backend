from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        (
            "problem_studio",
            "0002_problemstudiovoiceprofile_problemstudiovoicesample_and_more",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="problemstudiodocumentstyle",
            name="body_letter_spacing_percent",
            field=models.SmallIntegerField(db_default=0, default=0),
        ),
        migrations.AddField(
            model_name="problemstudiodocumentstyle",
            name="body_width_ratio_percent",
            field=models.PositiveSmallIntegerField(db_default=100, default=100),
        ),
        migrations.AddField(
            model_name="problemstudiodocumentstyle",
            name="match_source_style",
            field=models.BooleanField(db_default=True, default=True),
        ),
    ]
