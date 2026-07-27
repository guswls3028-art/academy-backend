from django.db import models
from django.utils import timezone

from .base import TimestampModel


class PlatformPushOutbox(TimestampModel):
    """Durable, deduplicated delivery queue for platform inbox notifications."""

    class Kind(models.TextChoices):
        CONTACT = "contact", "Contact"
        BUG = "bug", "Bug"
        FEEDBACK = "feedback", "Feedback"
        INCIDENT = "incident", "Incident"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        SENT = "sent", "Sent"
        DEAD = "dead", "Dead"

    kind = models.CharField(max_length=16, choices=Kind.choices)
    item_id = models.PositiveBigIntegerField()
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "platform_push_outbox"
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "item_id"],
                name="uniq_platform_push_item",
            ),
        ]
        indexes = [
            models.Index(
                fields=["status", "next_attempt_at"],
                name="platform_push_due_idx",
            ),
        ]
