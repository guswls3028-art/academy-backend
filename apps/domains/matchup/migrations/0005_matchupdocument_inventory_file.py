# Generated for storage-as-canonical migration (M-1)
# Adds nullable FK MatchupDocument.inventory_file → InventoryFile.
# M-2 backfill 커맨드 후 M-3에서 NOT NULL 전환 예정.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0001_initial"),
        ("matchup", "0004_matchupdocument_meta"),
    ]

    operations = [
        migrations.AddField(
            model_name="matchupdocument",
            name="inventory_file",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="matchup_document",
                to="inventory.inventoryfile",
            ),
        ),
    ]
