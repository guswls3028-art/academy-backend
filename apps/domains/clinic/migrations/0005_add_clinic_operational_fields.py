# PATH: apps/domains/clinic/migrations/0005_add_clinic_operational_fields.py

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("clinic", "0004_add_session_duration_minutes"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="sessionparticipant",
            name="participant_role",
            field=models.CharField(
                choices=[("target", "Target"), ("manual", "Manual")],
                default="target",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="sessionparticipant",
            name="status_changed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="sessionparticipant",
            name="status_changed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="clinic_participant_status_changes",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="sessionparticipant",
            name="checked_in_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="sessionparticipant",
            name="is_late",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="submission",
            name="graded_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
