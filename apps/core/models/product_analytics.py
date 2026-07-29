from __future__ import annotations

from django.db import models

from apps.core.models.tenant import Tenant
from apps.core.product_analytics.constants import EVENT_TYPES


class ProductUsageEvent(models.Model):
    event_id = models.UUIDField(unique=True)
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.PROTECT,
        related_name="product_usage_events",
    )
    actor_hash = models.CharField(max_length=64)
    role = models.CharField(max_length=20)
    audience_group = models.CharField(max_length=20)
    session_id = models.UUIDField()
    view_id = models.UUIDField()
    interaction_id = models.UUIDField(null=True, blank=True)
    event_type = models.CharField(
        max_length=24,
        choices=[(value, value) for value in EVENT_TYPES],
    )
    feature_id = models.CharField(max_length=80)
    screen_id = models.CharField(max_length=100)
    surface = models.CharField(max_length=16)
    route_template = models.CharField(max_length=180)
    cta_id = models.CharField(max_length=80, blank=True, default="")
    action_id = models.CharField(max_length=80, blank=True, default="")
    placement_id = models.CharField(max_length=80, blank=True, default="")
    position_index = models.PositiveSmallIntegerField(null=True, blank=True)
    failure_category = models.CharField(max_length=20, blank=True, default="")
    device_class = models.CharField(max_length=12)
    client_release = models.CharField(max_length=64)
    catalog_version = models.CharField(max_length=32)
    occurred_at = models.DateTimeField()
    received_at = models.DateTimeField(auto_now_add=True)
    synthetic = models.BooleanField(default=False)
    is_impersonated = models.BooleanField(default=False)

    class Meta:
        app_label = "core"
        indexes = [
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
                fields=["cta_id", "placement_id", "event_type", "occurred_at"],
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
        ]


class ProductUsageDailyActor(models.Model):
    day = models.DateField()
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.PROTECT,
        related_name="product_usage_daily_actors",
    )
    actor_hash = models.CharField(max_length=64)
    role = models.CharField(max_length=20)
    audience_group = models.CharField(max_length=20)
    surface = models.CharField(max_length=16)
    feature_id = models.CharField(max_length=80)
    screen_id = models.CharField(max_length=100)
    event_type = models.CharField(max_length=24)
    cta_id = models.CharField(max_length=80, blank=True, default="")
    action_id = models.CharField(max_length=80, blank=True, default="")
    placement_id = models.CharField(max_length=80, blank=True, default="")
    position_index = models.SmallIntegerField(default=-1)
    device_class = models.CharField(max_length=12)
    client_release = models.CharField(max_length=64)
    catalog_version = models.CharField(max_length=32)
    synthetic = models.BooleanField(default=False)
    is_impersonated = models.BooleanField(default=False)
    count = models.PositiveIntegerField()
    first_at = models.DateTimeField()
    last_at = models.DateTimeField()

    class Meta:
        app_label = "core"
        constraints = [
            models.UniqueConstraint(
                fields=[
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
                ],
                name="pua_daily_dims_uniq",
            )
        ]
        indexes = [
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
        ]
