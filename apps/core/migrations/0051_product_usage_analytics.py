import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0050_platformpushoutbox"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProductUsageEvent",
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
                ("event_id", models.UUIDField(unique=True)),
                ("actor_hash", models.CharField(max_length=64)),
                ("role", models.CharField(max_length=20)),
                ("audience_group", models.CharField(max_length=20)),
                ("session_id", models.UUIDField()),
                ("view_id", models.UUIDField()),
                ("interaction_id", models.UUIDField(blank=True, null=True)),
                (
                    "event_type",
                    models.CharField(
                        choices=[
                            ("screen_view", "screen_view"),
                            ("screen_engaged", "screen_engaged"),
                            ("cta_impression", "cta_impression"),
                            ("cta_click", "cta_click"),
                            ("task_start", "task_start"),
                            ("task_success", "task_success"),
                            ("task_failure", "task_failure"),
                        ],
                        max_length=24,
                    ),
                ),
                ("feature_id", models.CharField(max_length=80)),
                ("screen_id", models.CharField(max_length=100)),
                ("surface", models.CharField(max_length=16)),
                ("route_template", models.CharField(max_length=180)),
                ("cta_id", models.CharField(blank=True, default="", max_length=80)),
                (
                    "action_id",
                    models.CharField(blank=True, default="", max_length=80),
                ),
                (
                    "placement_id",
                    models.CharField(blank=True, default="", max_length=80),
                ),
                (
                    "position_index",
                    models.PositiveSmallIntegerField(blank=True, null=True),
                ),
                (
                    "failure_category",
                    models.CharField(blank=True, default="", max_length=20),
                ),
                ("device_class", models.CharField(max_length=12)),
                ("client_release", models.CharField(max_length=64)),
                ("catalog_version", models.CharField(max_length=32)),
                ("occurred_at", models.DateTimeField()),
                ("received_at", models.DateTimeField(auto_now_add=True)),
                ("synthetic", models.BooleanField(default=False)),
                ("is_impersonated", models.BooleanField(default=False)),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="product_usage_events",
                        to="core.tenant",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["tenant", "occurred_at"],
                        name="pua_tenant_occ_idx",
                    ),
                    models.Index(
                        fields=["role", "occurred_at"],
                        name="pua_role_occ_idx",
                    ),
                    models.Index(
                        fields=["feature_id", "event_type", "occurred_at"],
                        name="pua_feature_evt_idx",
                    ),
                    models.Index(
                        fields=["screen_id", "event_type", "occurred_at"],
                        name="pua_screen_evt_idx",
                    ),
                    models.Index(
                        fields=[
                            "cta_id",
                            "placement_id",
                            "event_type",
                            "occurred_at",
                        ],
                        name="pua_cta_evt_idx",
                    ),
                    models.Index(
                        fields=["interaction_id"],
                        name="pua_interaction_idx",
                    ),
                    models.Index(
                        fields=["synthetic", "is_impersonated", "occurred_at"],
                        name="pua_quality_occ_idx",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="ProductUsageDailyActor",
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
                ("day", models.DateField()),
                ("actor_hash", models.CharField(max_length=64)),
                ("role", models.CharField(max_length=20)),
                ("audience_group", models.CharField(max_length=20)),
                ("surface", models.CharField(max_length=16)),
                ("feature_id", models.CharField(max_length=80)),
                ("screen_id", models.CharField(max_length=100)),
                ("event_type", models.CharField(max_length=24)),
                ("cta_id", models.CharField(blank=True, default="", max_length=80)),
                (
                    "action_id",
                    models.CharField(blank=True, default="", max_length=80),
                ),
                (
                    "placement_id",
                    models.CharField(blank=True, default="", max_length=80),
                ),
                ("position_index", models.SmallIntegerField(default=-1)),
                ("device_class", models.CharField(max_length=12)),
                ("client_release", models.CharField(max_length=64)),
                ("catalog_version", models.CharField(max_length=32)),
                ("synthetic", models.BooleanField(default=False)),
                ("is_impersonated", models.BooleanField(default=False)),
                ("count", models.PositiveIntegerField()),
                ("first_at", models.DateTimeField()),
                ("last_at", models.DateTimeField()),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="product_usage_daily_actors",
                        to="core.tenant",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["day", "tenant"],
                        name="pua_daily_tenant_idx",
                    ),
                    models.Index(
                        fields=["day", "role", "surface"],
                        name="pua_daily_role_idx",
                    ),
                    models.Index(
                        fields=["day", "feature_id", "event_type"],
                        name="pua_daily_feature_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=(
                            "day",
                            "tenant",
                            "actor_hash",
                            "role",
                            "audience_group",
                            "surface",
                            "feature_id",
                            "screen_id",
                            "event_type",
                            "cta_id",
                            "action_id",
                            "placement_id",
                            "position_index",
                            "device_class",
                            "client_release",
                            "catalog_version",
                            "synthetic",
                            "is_impersonated",
                        ),
                        name="pua_daily_dims_uniq",
                    ),
                ],
            },
        ),
    ]
