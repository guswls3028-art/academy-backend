"""Django model-discovery entry point for the teacher app."""

import uuid

from django.conf import settings
from django.db import models

from apps.api.common.models import TimestampModel

from .push.models import PushNotificationConfig, PushSubscription


class TeacherOpsExecution(TimestampModel):
    """Idempotency receipt for one confirmed teacher-assistant proposal."""

    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        related_name="teacher_ops_executions",
    )
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="teacher_ops_executions",
    )
    proposal_digest = models.CharField(max_length=64)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PROCESSING,
        db_index=True,
    )
    result = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=64, blank=True, default="")
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["tenant", "actor_user", "-created_at"],
                name="teacher_ops_actor_idx",
            ),
        ]


__all__ = [
    "PushNotificationConfig",
    "PushSubscription",
    "TeacherOpsExecution",
]
