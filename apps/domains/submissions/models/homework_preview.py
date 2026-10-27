from __future__ import annotations

import uuid

from django.db import models

from apps.api.common.models import TimestampModel


class HomeworkMediaPreview(TimestampModel):
    """Private, replaceable browser-compatible copy; the submission stays original."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("core.Tenant", on_delete=models.CASCADE)
    submission = models.ForeignKey("submissions.Submission", on_delete=models.CASCADE, related_name="compatible_previews")
    source_media_id = models.CharField(max_length=64)
    source_key = models.CharField(max_length=512)
    source_fingerprint = models.CharField(max_length=64)
    object_key = models.CharField(max_length=512, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    attempt_token = models.UUIDField(default=uuid.uuid4)
    attempt_count = models.PositiveIntegerField(default=1)
    batch_job_id = models.CharField(max_length=128, blank=True, default="")
    error_code = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "submission", "source_media_id", "source_fingerprint"], name="uniq_homework_preview_source")]
        indexes = [models.Index(fields=["status", "updated_at"], name="homework_preview_status_idx")]
