# PATH: apps/core/migrations/0005_make_attendance_expense_tenant_not_null.py
from __future__ import annotations

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_backfill_attendance_expense_tenant"),
    ]

    operations = [
        migrations.AlterField(
            model_name="attendance",
            name="tenant",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="attendances",
                to="core.tenant",
                null=False,
                blank=False,
            ),
        ),
        migrations.AlterField(
            model_name="expense",
            name="tenant",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="expenses",
                to="core.tenant",
                null=False,
                blank=False,
            ),
        ),
    ]
