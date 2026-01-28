# PATH: apps/domains/homework_results/models/score.py

from __future__ import annotations

from django.db import models

from apps.api.common.models import TimestampModel
from apps.domains.lectures.models import Session
from apps.domains.homework_results.models.homework import Homework  # ✅ 추가


class HomeworkScore(TimestampModel):
    """
    Enrollment x Session x Homework 단위 숙제 점수/승인 스냅샷
    """

    class LockReason(models.TextChoices):
        GRADING = "GRADING", "채점중"
        PUBLISHED = "PUBLISHED", "게시됨"
        MANUAL = "MANUAL", "수동잠금"
        OTHER = "OTHER", "기타"

    enrollment_id = models.PositiveIntegerField(db_index=True)

    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        related_name="homework_scores",
    )

    # ✅ NEW: 어떤 과제에 대한 점수인지
    homework = models.ForeignKey(
        Homework,
        on_delete=models.CASCADE,
        related_name="scores",
        db_index=True,
    )

    # percent 또는 raw/max 둘 다 지원
    score = models.FloatField(null=True, blank=True)
    max_score = models.FloatField(null=True, blank=True)

    teacher_approved = models.BooleanField(default=False)

    passed = models.BooleanField(default=False)
    clinic_required = models.BooleanField(default=False)

    is_locked = models.BooleanField(default=False)
    lock_reason = models.CharField(
        max_length=30,
        choices=LockReason.choices,
        null=True,
        blank=True,
    )

    updated_by_user_id = models.PositiveIntegerField(null=True, blank=True)
    meta = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "homework_results_homeworkscore"

        # ❗ 기존 unique 제약 수정 필요
        constraints = [
            models.UniqueConstraint(
                fields=["enrollment_id", "session", "homework"],
                name="uniq_hwscore_enrollment_session_homework",
            )
        ]

        indexes = [
            models.Index(
                fields=["enrollment_id", "updated_at"],
                name="hwres_enroll_upd_idx",
            ),
            models.Index(
                fields=["session", "updated_at"],
                name="hwres_session_upd_idx",
            ),
            models.Index(
                fields=["homework", "updated_at"],
                name="hwres_homework_upd_idx",
            ),
        ]

        ordering = ["-updated_at", "-id"]

    def __str__(self) -> str:
        return (
            f"HomeworkScore("
            f"enroll={self.enrollment_id}, "
            f"session={self.session_id}, "
            f"homework={self.homework_id}, "
            f"score={self.score})"
        )
