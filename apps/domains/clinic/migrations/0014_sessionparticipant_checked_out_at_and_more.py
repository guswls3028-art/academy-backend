import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("clinic", "0013_add_section_to_session"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="sessionparticipant",
            name="checked_out_at",
            field=models.DateTimeField(
                blank=True,
                help_text="클리닉 하원 처리 시각",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="sessionparticipant",
            name="checked_out_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="clinic_check_outs",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
