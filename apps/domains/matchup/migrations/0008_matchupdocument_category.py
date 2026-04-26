from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("matchup", "0007_alter_matchupdocument_inventory_file"),
    ]

    operations = [
        migrations.AddField(
            model_name="matchupdocument",
            name="category",
            field=models.CharField(blank=True, db_index=True, default="", max_length=100),
        ),
    ]
