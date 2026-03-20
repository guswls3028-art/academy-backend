# Generated manually: Convert integer enrollment_id to ForeignKey
# SessionParticipant.enrollment_id → enrollment FK(SET_NULL)

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("enrollment", "0001_initial"),
        ("clinic", "0007_fix_rebooking_constraint"),
    ]

    operations = [
        # enrollment_id (PositiveIntegerField, NULL) → ForeignKey(SET_NULL, NULL)
        migrations.AlterField(
            model_name="sessionparticipant",
            name="enrollment_id",
            field=models.ForeignKey(
                blank=True,
                db_column="enrollment_id",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="clinic_participations",
                to="enrollment.enrollment",
            ),
        ),
        migrations.RenameField(
            model_name="sessionparticipant",
            old_name="enrollment_id",
            new_name="enrollment",
        ),
    ]
