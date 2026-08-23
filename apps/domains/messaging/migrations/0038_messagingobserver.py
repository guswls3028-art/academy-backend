from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("messaging", "0037_alter_schedulednotification_payload"),
    ]

    operations = [
        migrations.CreateModel(
            name="MessagingObserver",
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
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="messaging_observers",
                        to="core.tenant",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="messaging_observer_assignments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["tenant_id", "user_id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("tenant", "user"),
                        name="uniq_messaging_observer_tenant_user",
                    ),
                ],
            },
        ),
    ]
