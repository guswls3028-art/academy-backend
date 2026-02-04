# PATH: apps/domains/parents/migrations/0002_add_parent_tenant.py
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
        ("parents", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="parent",
            name="tenant",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="parents",
                to="core.tenant",
                null=True,
                blank=True,
            ),
        ),
    ]
