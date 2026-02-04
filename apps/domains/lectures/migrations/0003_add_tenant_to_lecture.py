# PATH: apps/domains/lectures/migrations/0003_add_tenant_to_lecture.py

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("lectures", "0002_remove_session_exam"),
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="lecture",
            name="tenant",
            field=models.ForeignKey(
                to="core.tenant",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="lectures",
                null=True,
            ),
        ),
        migrations.RunSQL(
            sql="UPDATE lectures_lecture SET tenant_id = 1 WHERE tenant_id IS NULL;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AlterField(
            model_name="lecture",
            name="tenant",
            field=models.ForeignKey(
                to="core.tenant",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="lectures",
            ),
        ),
        migrations.AddConstraint(
            model_name="lecture",
            constraint=models.UniqueConstraint(
                fields=("tenant", "title"),
                name="uniq_lecture_title_per_tenant",
            ),
        ),
    ]
