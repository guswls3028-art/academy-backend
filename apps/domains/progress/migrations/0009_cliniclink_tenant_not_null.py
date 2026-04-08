# Step 3 of 3: Make ClinicLink.tenant NOT NULL
# Safe because:
# - 0008 backfilled all existing rows from enrollment.tenant_id
# - All code paths that create ClinicLink now explicitly set tenant_id
# - Zero-downtime: the column already has data, this only adds a constraint

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0030_set_dnb_school_level_mode"),
        ("progress", "0008_cliniclink_backfill_tenant"),
    ]

    operations = [
        migrations.AlterField(
            model_name="cliniclink",
            name="tenant",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="clinic_links",
                to="core.tenant",
            ),
        ),
    ]
