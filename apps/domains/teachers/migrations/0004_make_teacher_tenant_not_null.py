# PATH: apps/domains/teachers/migrations/0004_make_teacher_tenant_not_null.py
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("teachers", "0003_backfill_teacher_tenant"),
    ]

    operations = [
        migrations.AlterField(
            model_name="teacher",
            name="tenant",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="teachers",
                to="core.tenant",
                null=False,
                blank=False,
            ),
        ),
    ]
