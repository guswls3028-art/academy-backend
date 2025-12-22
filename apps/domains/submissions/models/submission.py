# apps/domains/submissions/models/submission.py
from __future__ import annotations

from django.db import models
from django.conf import settings

from apps.api.common.models import TimestampModel


class Submission(TimestampModel):
    """
    submissions = "제출 행위 + 원본 보관"
    - 계산/채점/정답비교 금지
    """

    class TargetType(models.TextChoices):
        EXAM = "exam", "Exam"
        HOMEWORK = "homework", "Homework"

    class Source(models.TextChoices):
        # 시험
        OMR_SCAN = "omr_scan", "OMR Scan"
        OMR_MANUAL = "omr_manual", "OMR Manual Input"
        ONLINE = "online", "Online"
        # 숙제
        HOMEWORK_IMAGE = "homework_image", "Homework Image"
        HOMEWORK_VIDEO = "homework_video", "Homework Video"
        # 기타
        AI_MATCH = "ai_match", "AI Image Match"

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        DISPATCHED = "dispatched", "Dispatched"            # AI job publish 완료
        EXTRACTING = "extracting", "Extracting"            # (선택) worker 처리중 표현
        ANSWERS_READY = "answers_ready", "Answers Ready"   # SubmissionAnswer 저장 완료
        GRADING = "grading", "Grading"                     # results 채점 job 처리 중
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="submissions",
    )

    # ✅ A안: enrollment_id 추가 (FK 강제 X)
    enrollment_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)

    target_type = models.CharField(max_length=20, choices=TargetType.choices)
    target_id = models.PositiveIntegerField()

    source = models.CharField(max_length=30, choices=Source.choices)

    file = models.FileField(
        upload_to="submissions/%Y/%m/%d/",
        null=True,
        blank=True,
    )

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

    def __str__(self) -> str:
        return (
            f"Submission({self.id}) {self.target_type}:{self.target_id} "
            f"source={self.source} by user={self.user_id}"
        )
