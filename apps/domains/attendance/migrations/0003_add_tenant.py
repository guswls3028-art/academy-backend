# PATH: apps/domains/attendance/migrations/0003_add_tenant.py

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
        ("attendance", "0002_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="attendance",
            name="tenant",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="attendances",
                to="core.tenant",
            ),
        ),
    ]
