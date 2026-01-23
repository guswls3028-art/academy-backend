# PATH: apps/domains/homework_results/models/score.py
"""
Homework Results Domain Models (Score Snapshot)

✅ 이 도메인은 "Homework 결과 스냅샷"만 담당한다.
- Homework 정의/정책(homework 도메인)과 분리
- results(exam 결과)와 동일한 역할의 "결과 레이어"

✅ DB 정합
- 이 DB에서는 homework_homeworkscore 테이블이 존재하지 않음
- 따라서 homework_results_homeworkscore 를 정식 테이블로 사용한다.
"""

from __future__ import annotations

from django.db import models

from apps.api.common.models import TimestampModel
from apps.domains.lectures.models import Session


class HomeworkScore(TimestampModel):
    """
    Enrollment x Session 단위 숙제 점수/승인 스냅샷
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
        # ✅ 여기 반드시 고정
        db_table = "homework_results_homeworkscore"

        constraints = [
            models.UniqueConstraint(
                fields=["enrollment_id", "session"],
                name="unique_homework_score_per_enrollment_session",
            )
        ]

        # ✅ DB에 이미 존재하는 인덱스명과 일치해야 함
        indexes = [
            models.Index(fields=["enrollment_id", "updated_at"], name="hwres_enroll_upd_idx"),
            models.Index(fields=["session", "updated_at"], name="hwres_session_upd_idx"),
        ]

        ordering = ["-updated_at", "-id"]

    def __str__(self) -> str:
        return f"HomeworkScore(enroll={self.enrollment_id}, session={self.session_id}, score={self.score})"
