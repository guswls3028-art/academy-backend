from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.api.common.models import TimestampModel
from apps.core.models import Tenant


class OmrUploadBatch(TimestampModel):
    """Durable, tenant-scoped admission envelope for one OMR file selection."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="omr_upload_batches",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="omr_upload_batches",
    )
    exam_id = models.PositiveBigIntegerField()
    session_id = models.PositiveBigIntegerField(null=True, blank=True)
    lecture_id = models.PositiveBigIntegerField(null=True, blank=True)
    total_count = models.PositiveSmallIntegerField()
    completion_notice_claimed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["tenant", "created_by", "created_at"],
                name="omr_batch_owner_created_idx",
            ),
            models.Index(
                fields=["tenant", "exam_id", "created_at"],
                name="omr_batch_exam_created_idx",
            ),
        ]


class OmrUploadBatchItem(TimestampModel):
    class AdmissionStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        RECEIVED = "received", "Received"
        DUPLICATE = "duplicate", "Duplicate"
        FAILED = "failed", "Failed"

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="omr_upload_batch_items",
    )
    exam_id = models.PositiveBigIntegerField()
    batch = models.ForeignKey(
        OmrUploadBatch,
        on_delete=models.CASCADE,
        related_name="items",
    )
    ordinal = models.PositiveSmallIntegerField()
    submission = models.OneToOneField(
        "submissions.Submission",
        on_delete=models.SET_NULL,
        related_name="omr_upload_batch_item",
        null=True,
        blank=True,
    )
    duplicate_of_submission = models.ForeignKey(
        "submissions.Submission",
        on_delete=models.SET_NULL,
        related_name="duplicate_omr_upload_items",
        null=True,
        blank=True,
    )
    content_sha256 = models.CharField(max_length=64, blank=True, default="")
    admission_status = models.CharField(
        max_length=16,
        choices=AdmissionStatus.choices,
        default=AdmissionStatus.PENDING,
    )
    failure_code = models.CharField(max_length=64, blank=True, default="")
    failure_message = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        ordering = ["ordinal"]
        indexes = [
            models.Index(
                fields=["batch", "admission_status", "ordinal"],
                name="omr_item_batch_status_idx",
            ),
            models.Index(
                fields=["tenant", "exam_id", "content_sha256"],
                name="omr_item_exam_hash_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "ordinal"],
                name="uniq_omr_batch_item_ordinal",
            ),
            models.UniqueConstraint(
                fields=["tenant", "exam_id", "content_sha256"],
                condition=(
                    models.Q(admission_status="received")
                    & ~models.Q(content_sha256="")
                ),
                name="uniq_received_omr_exam_content",
            ),
        ]
