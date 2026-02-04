# PATH: apps/domains/teachers/migrations/0002_add_teacher_tenant.py
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
        ("teachers", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="teacher",
            name="tenant",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="teachers",
                to="core.tenant",
                null=True,
                blank=True,
            ),
        ),
    ]
