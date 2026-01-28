# PATH: apps/domains/clinic/migrations/0003_participant_operational_fields.py
# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("clinic", "0002_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="sessionparticipant",
            name="source",
            field=models.CharField(
                choices=[("auto", "Auto"), ("manual", "Manual")],
                default="auto",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="sessionparticipant",
            name="enrollment_id",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="sessionparticipant",
            name="clinic_reason",
            field=models.CharField(
                blank=True,
                null=True,
                choices=[("exam", "Exam"), ("homework", "Homework"), ("both", "Both")],
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="sessionparticipant",
            name="status",
            field=models.CharField(
                choices=[
                    ("booked", "Booked"),
                    ("attended", "Attended"),
                    ("no_show", "NoShow"),
                    ("cancelled", "Cancelled"),
                ],
                default="booked",
                max_length=20,
            ),
        ),
    ]
