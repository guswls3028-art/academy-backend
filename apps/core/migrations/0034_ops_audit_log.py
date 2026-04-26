# Generated for OpsAuditLog (manual)

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0033_add_user_token_version"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="OpsAuditLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("actor_username", models.CharField(blank=True, default="", max_length=150)),
                ("action", models.CharField(db_index=True, max_length=64)),
                ("summary", models.CharField(blank=True, default="", max_length=255)),
                ("payload", models.JSONField(blank=True, default=dict)),
                (
                    "result",
                    models.CharField(
                        choices=[("success", "Success"), ("failed", "Failed")],
                        default="success",
                        max_length=16,
                    ),
                ),
                ("error", models.CharField(blank=True, default="", max_length=255)),
                ("ip", models.CharField(blank=True, default="", max_length=64)),
                ("user_agent", models.CharField(blank=True, default="", max_length=255)),
                (
                    "actor_user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "target_tenant",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="core.tenant",
                    ),
                ),
                (
                    "target_user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "ops_audit_log",
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["-created_at"], name="ops_audit_l_created_idx"),
                    models.Index(fields=["action", "-created_at"], name="ops_audit_l_action_idx"),
                    models.Index(fields=["target_tenant", "-created_at"], name="ops_audit_l_tenant_idx"),
                    models.Index(fields=["actor_user", "-created_at"], name="ops_audit_l_actor_idx"),
                ],
            },
        ),
    ]
