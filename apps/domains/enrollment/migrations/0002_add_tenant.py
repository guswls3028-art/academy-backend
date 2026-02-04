# PATH: apps/domains/enrollment/migrations/0002_add_tenant.py

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
        ("enrollment", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="enrollment",
            name="tenant",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="enrollments",
                to="core.tenant",
            ),
        ),
        migrations.AddField(
            model_name="sessionenrollment",
            name="tenant",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="session_enrollments",
                to="core.tenant",
            ),
        ),
    ]
