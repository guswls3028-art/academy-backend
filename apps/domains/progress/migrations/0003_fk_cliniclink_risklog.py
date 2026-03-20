from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("enrollment", "0001_initial"),
        ("progress", "0002_fk_conversion"),
    ]

    operations = [
        migrations.AlterField(
            model_name="cliniclink",
            name="enrollment_id",
            field=models.ForeignKey(
                db_column="enrollment_id",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="clinic_links",
                to="enrollment.enrollment",
            ),
        ),
        migrations.RenameField(
            model_name="cliniclink",
            old_name="enrollment_id",
            new_name="enrollment",
        ),
        migrations.AlterField(
            model_name="risklog",
            name="enrollment_id",
            field=models.ForeignKey(
                db_column="enrollment_id",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="risk_logs",
                to="enrollment.enrollment",
            ),
        ),
        migrations.RenameField(
            model_name="risklog",
            old_name="enrollment_id",
            new_name="enrollment",
        ),
    ]
