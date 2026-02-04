# PATH: apps/domains/students/migrations/0002_add_tenant.py

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
        ("students", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="student",
            name="tenant",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="students",
                to="core.tenant",
                default=1,  # ⚠️ 초기 데이터용 (운영에서 조정)
            ),
            preserve_default=False,
        ),
    ]
