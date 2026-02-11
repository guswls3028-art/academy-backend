# PATH: apps/core/migrations/0012_program_plan.py
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0011_alter_tenantdomain_is_primary"),
    ]

    operations = [
        migrations.AddField(
            model_name="program",
            name="plan",
            field=models.CharField(
                max_length=20,
                choices=[
                    ("lite", "Lite"),
                    ("basic", "Basic"),
                    ("premium", "Premium"),
                ],
                default="premium",
            ),
        ),
    ]
