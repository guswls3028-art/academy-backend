# M-3: 0006 backfill 후 inventory_file을 NOT NULL로 전환.
# storage-as-canonical 모델 확정.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0001_initial"),
        ("matchup", "0006_backfill_matchup_inventory"),
    ]

    operations = [
        migrations.AlterField(
            model_name="matchupdocument",
            name="inventory_file",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="matchup_document",
                to="inventory.inventoryfile",
            ),
        ),
    ]
