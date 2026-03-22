"""Add completed_at and completed_by to SessionParticipant for self-study completion tracking."""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("clinic", "0008_fk_enrollment_conversion"),
    ]

    operations = [
        migrations.AddField(
            model_name="sessionparticipant",
            name="completed_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="자율학습 완료 시각",
            ),
        ),
        migrations.AddField(
            model_name="sessionparticipant",
            name="completed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="clinic_completions",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
