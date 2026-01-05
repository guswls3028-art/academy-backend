# PATH: apps/domains/submissions/models/submission.py
# 변경 요약:
# - FileField 제거
# - R2 기반 file_key / file_type 필드 추가

from __future__ import annotations

from django.db import models
from django.conf import settings
from apps.api.common.models import TimestampModel


class Submission(TimestampModel):
    """
    submissions = 제출 행위 + 원본 메타
    - 파일은 Object Storage(R2)에만 존재
    """

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

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="submissions",
    )

    enrollment_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)

    target_type = models.CharField(max_length=20, choices=TargetType.choices)
    target_id = models.PositiveIntegerField()

    source = models.CharField(max_length=30, choices=Source.choices)

    # ✅ R2 기준 파일 메타
    file_key = models.CharField(max_length=512, null=True, blank=True)
    file_type = models.CharField(max_length=50, null=True, blank=True)
    file_size = models.PositiveIntegerField(null=True, blank=True)

    payload = models.JSONField(null=True, blank=True)

    status = models.CharField(
        max_length=20,
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
        ]
