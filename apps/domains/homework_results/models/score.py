# PATH: apps/domains/homework_results/models/score.py

from __future__ import annotations

from django.db import models

from apps.api.common.models import TimestampModel
from apps.domains.lectures.models import Session
from apps.domains.homework_results.models.homework import Homework


class HomeworkScore(TimestampModel):
    """
    Enrollment x Session x Homework 단위 숙제 점수/승인 스냅샷

    ✅ 상태(운영 기준) — DB 표현 (고정)
    - 미입력     : score=None & meta.status=None
    - 미제출     : meta.status="NOT_SUBMITTED"   (0점과 다름 / 클리닉 대상)
    - 0점        : score=0
    - 정상 점수  : score>=0

    ❗RULE 3: meta(JSONField)는 확장 정보만 담는다.
    단, 본 모델은 "미제출" 상태를 meta.status로만 표현한다(마이그레이션 없이 확장).
    """

    class LockReason(models.TextChoices):
        GRADING = "GRADING", "채점중"
        PUBLISHED = "PUBLISHED", "게시됨"
        MANUAL = "MANUAL", "수동잠금"
        OTHER = "OTHER", "기타"

    class MetaStatus:
        """
        ✅ meta.status enum (고정)
        - NOT_SUBMITTED: 숙제 미제출(클리닉 대상)
        """
        NOT_SUBMITTED = "NOT_SUBMITTED"

    enrollment_id = models.PositiveIntegerField(db_index=True)

    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        related_name="homework_scores",
    )

    homework = models.ForeignKey(
        Homework,
        on_delete=models.CASCADE,
        related_name="scores",
        db_index=True,
    )

    # percent 또는 raw/max 형태 모두 지원
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

    # ✅ 확장 필드(마이그레이션 없이): meta.status 만 사용
    meta = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "homework_results_homeworkscore"

        constraints = [
            models.UniqueConstraint(
                fields=["enrollment_id", "session", "homework"],
                name="uniq_hwscore_enrollment_session_homework",
            )
        ]

        # ✅ 운영 성능 필수 인덱스 (삭제 금지: RULE 2)
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
