# PATH: apps/domains/parents/migrations/0004_make_parent_tenant_not_null.py
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("parents", "0003_backfill_parent_tenant"),
    ]

    operations = [
        migrations.AlterField(
            model_name="parent",
            name="tenant",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="parents",
                to="core.tenant",
                null=False,
                blank=False,
            ),
        ),
    ]
