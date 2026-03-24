# PATH: apps/domains/submissions/models/submission.py
from __future__ import annotations

from django.db import models
from django.conf import settings
from apps.api.common.models import TimestampModel
from apps.core.models import Tenant


class Submission(TimestampModel):
    class TargetType(models.TextChoices):
        EXAM = "exam", "Exam"
        HOMEWORK = "homework", "Homework"

    class Source(models.TextChoices):
        OMR_SCAN = "omr_scan", "OMR Scan"
        OMR_MANUAL = "omr_manual", "OMR Manual Input"
        ONLINE = "online", "Online"
        HOMEWORK_IMAGE = "homework_image", "Homework Image"
        HOMEWORK_VIDEO = "homework_video", "Homework Video"
        AI_MATCH = "ai_match", "AI Image Match"

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        DISPATCHED = "dispatched", "Dispatched"
        EXTRACTING = "extracting", "Extracting"
        ANSWERS_READY = "answers_ready", "Answers Ready"
        GRADING = "grading", "Grading"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"
        NEEDS_IDENTIFICATION = "needs_identification", "Needs Identification"
        SUPERSEDED = "superseded", "Superseded"  # 재응시로 대체됨

    # ──────────────────────────────────────────────
    # 상태 전이 SSOT → apps/domains/submissions/services/transition.py
    # 모든 상태 변경은 transition.transit() 를 통해서만 수행할 것.
    # ──────────────────────────────────────────────

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="submissions",
        db_index=True,  # ✅ tenant_id 인덱스 추가
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="submissions",
    )

    enrollment_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)

    target_type = models.CharField(max_length=20, choices=TargetType.choices)
    target_id = models.PositiveIntegerField()

    source = models.CharField(max_length=30, choices=Source.choices)

    file_key = models.CharField(max_length=512, null=True, blank=True)
    file_type = models.CharField(max_length=50, null=True, blank=True)
    file_size = models.PositiveIntegerField(null=True, blank=True)

    payload = models.JSONField(null=True, blank=True)

    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.SUBMITTED,
    )
    error_message = models.TextField(blank=True)
    meta = models.JSONField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["target_type", "target_id"]),
            models.Index(fields=["enrollment_id", "created_at"]),
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["status"]),
            models.Index(fields=["source"]),
            models.Index(fields=["tenant", "created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "target_type", "target_id"],
                condition=models.Q(
                    status__in=[
                        "submitted", "dispatched", "extracting",
                        "answers_ready", "grading",
                    ],
                    # OMR batch upload: staff가 여러 학생 답안지를 업로드하므로
                    # 같은 user+exam에 복수 active submission 허용 필요
                    source__in=["online", "omr_manual", "homework_image",
                                "homework_video", "ai_match"],
                ),
                name="unique_active_submission_per_target",
            ),
        ]
