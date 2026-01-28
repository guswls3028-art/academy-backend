# PATH: apps/domains/clinic/migrations/0004_add_session_duration_minutes.py

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("clinic", "0003_participant_operational_fields"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="session",
            name="duration_minutes",
            field=models.PositiveIntegerField(default=60),
        ),
        migrations.AddField(
            model_name="session",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="created_clinic_sessions",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
