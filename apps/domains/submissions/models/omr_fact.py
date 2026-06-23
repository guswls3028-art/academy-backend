from __future__ import annotations

from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.api.common.models import BaseModel
from apps.core.models import Tenant


class OMRRecognitionRun(BaseModel):
    """One AI recognition fact for an OMR scan submission."""

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="omr_recognition_runs",
    )
    submission = models.ForeignKey(
        "submissions.Submission",
        on_delete=models.CASCADE,
        related_name="omr_recognition_runs",
    )
    job_id = models.CharField(max_length=128, blank=True, db_index=True)
    status = models.CharField(max_length=32, db_index=True)
    worker_version = models.CharField(max_length=32, blank=True)
    answer_count = models.PositiveIntegerField(default=0)
    answer_status_counts = models.JSONField(default=dict, blank=True)
    aligned = models.BooleanField(null=True, blank=True)
    alignment_method = models.CharField(max_length=64, blank=True)
    contract_snapshot = models.JSONField(default=dict, blank=True)
    raw_result = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True, default="")
    received_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "submissions_omr_recognition_run"
        indexes = [
            models.Index(fields=["tenant", "submission", "-received_at"]),
            models.Index(fields=["status", "received_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["submission", "job_id"],
                condition=~Q(job_id=""),
                name="uniq_omr_recognition_submission_job",
            ),
        ]

    def __str__(self) -> str:
        return f"OMRRecognitionRun<{self.id}> sub={self.submission_id} job={self.job_id}"


class OMRDetectedAnswer(BaseModel):
    """Question-level detected answer fact for one recognition run."""

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="omr_detected_answers",
    )
    submission = models.ForeignKey(
        "submissions.Submission",
        on_delete=models.CASCADE,
        related_name="omr_detected_answers",
    )
    recognition_run = models.ForeignKey(
        OMRRecognitionRun,
        on_delete=models.CASCADE,
        related_name="detected_answers",
    )
    question_number = models.PositiveIntegerField(db_index=True)
    exam_question = models.ForeignKey(
        "exams.ExamQuestion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column="exam_question_id",
        related_name="omr_detected_answers",
        db_index=True,
    )
    answer = models.TextField(blank=True)
    detected = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=32, blank=True, db_index=True)
    marking = models.CharField(max_length=64, blank=True)
    confidence = models.FloatField(null=True, blank=True)
    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "submissions_omr_detected_answer"
        indexes = [
            models.Index(fields=["submission", "question_number"]),
            models.Index(fields=["recognition_run", "question_number"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["recognition_run", "question_number"],
                name="uniq_omr_detected_answer_run_question",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"OMRDetectedAnswer<{self.id}> "
            f"sub={self.submission_id} q={self.question_number}"
        )


class OMRStudentMatch(BaseModel):
    """Student match fact for an OMR scan, including manual match history."""

    class Status(models.TextChoices):
        CONFIRMED = "confirmed", "Confirmed"
        NEEDS_REVIEW = "needs_review", "Needs Review"
        UNMATCHED = "unmatched", "Unmatched"
        DUPLICATE = "duplicate", "Duplicate"

    class Method(models.TextChoices):
        AUTO_IDENTIFIER = "auto_identifier", "Auto Identifier"
        MANUAL = "manual", "Manual"
        PRESERVED_MANUAL = "preserved_manual", "Preserved Manual"

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="omr_student_matches",
    )
    submission = models.ForeignKey(
        "submissions.Submission",
        on_delete=models.CASCADE,
        related_name="omr_student_matches",
    )
    enrollment = models.ForeignKey(
        "enrollment.Enrollment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column="enrollment_id",
        related_name="omr_student_matches",
        db_index=True,
    )
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.NEEDS_REVIEW,
        db_index=True,
    )
    method = models.CharField(
        max_length=32,
        choices=Method.choices,
        default=Method.AUTO_IDENTIFIER,
        db_index=True,
    )
    identifier_status = models.CharField(max_length=64, blank=True)
    identifier_payload = models.JSONField(default=dict, blank=True)
    confidence = models.FloatField(null=True, blank=True)
    actor = models.CharField(max_length=128, blank=True)
    is_current = models.BooleanField(default=True, db_index=True)
    matched_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "submissions_omr_student_match"
        indexes = [
            models.Index(fields=["tenant", "submission", "is_current"]),
            models.Index(fields=["tenant", "enrollment", "matched_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["submission"],
                condition=Q(is_current=True),
                name="uniq_current_omr_student_match",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"OMRStudentMatch<{self.id}> "
            f"sub={self.submission_id} enrollment={self.enrollment_id}"
        )
