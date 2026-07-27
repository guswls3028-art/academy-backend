from django.conf import settings
from django.db import models

from .base import TimestampModel


class PlatformInboxIncidentState(TimestampModel):
    """Queryable operator state for an immutable manual incident audit record."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        RESOLVED = "resolved", "Resolved"

    incident = models.OneToOneField(
        "core.OpsAuditLog",
        on_delete=models.CASCADE,
        related_name="inbox_state",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
    )
    admin_memo = models.TextField(blank=True, default="")
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "platform_inbox_incident_state"
