import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0048_landingconsultrequest_resolved_at"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PlatformInboxIncidentState",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "status",
                    models.CharField(
                        choices=[("open", "Open"), ("resolved", "Resolved")],
                        db_index=True,
                        default="open",
                        max_length=16,
                    ),
                ),
                ("admin_memo", models.TextField(blank=True, default="")),
                (
                    "incident",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="inbox_state",
                        to="core.opsauditlog",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"db_table": "platform_inbox_incident_state"},
        ),
    ]
