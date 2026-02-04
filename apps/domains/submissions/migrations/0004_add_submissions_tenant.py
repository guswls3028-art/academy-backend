# PATH: apps/domains/submissions/migrations/0004_add_submissions_tenant.py
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
        ("submissions", "0003_alter_submission_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="submission",
            name="tenant",
            field=models.ForeignKey(
                to="core.tenant",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="submissions",
                null=True,
                blank=True,
            ),
        ),
        migrations.AddField(
            model_name="submissionanswer",
            name="tenant",
            field=models.ForeignKey(
                to="core.tenant",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="submission_answers",
                null=True,
                blank=True,
            ),
        ),
    ]
