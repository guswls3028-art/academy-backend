# PATH: apps/domains/submissions/migrations/0006_make_submissions_tenant_not_null.py
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("submissions", "0005_backfill_submissions_tenant"),
    ]

    operations = [
        migrations.AlterField(
            model_name="submission",
            name="tenant",
            field=models.ForeignKey(
                to="core.tenant",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="submissions",
                null=False,
                blank=False,
            ),
        ),
        migrations.AlterField(
            model_name="submissionanswer",
            name="tenant",
            field=models.ForeignKey(
                to="core.tenant",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="submission_answers",
                null=False,
                blank=False,
            ),
        ),
    ]
