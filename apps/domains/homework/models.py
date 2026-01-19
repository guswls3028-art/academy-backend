# PATH: apps/domains/homework/models.py
"""
Homework Domain Models

✅ 설계 고정(중요)
- homework 도메인은 "제출/채점"을 하지 않는다.
  - 제출/원본답안/상태: submissions 도메인
  - 시험 채점/결과: results 도메인
  - 차시 통과/집계: progress 도메인

✅ homework 도메인의 책임
- 운영자가 입력하는 숙제 점수/승인 여부
- 편집 LOCK 상태 (is_locked / lock_reason)
- 프론트 ScoreBlock의 단일 진실(초기 형태)

✅ PATCH 성공 시 backend 책임
- 연결된 Submission(homework fields)을 갱신하고
- progress pipeline을 즉시 트리거한다.
  (실제 트리거 코드는 views에서 수행)

✅ score: null 의미
- 아직 채점/평가가 확정되지 않은 상태 (Not graded)
"""

from __future__ import annotations

from django.db import models

from apps.api.common.models import TimestampModel
from apps.domains.lectures.models import Session


class HomeworkScore(TimestampModel):
    """
    Enrollment x Session 단위 숙제 점수/승인 스냅샷

    - 이 값은 "progress 계산에 직접 사용"되기보다는,
      progress pipeline이 읽는 Submission.homework_* 를 갱신하기 위한 운영 입력값이다.
    - 프론트는 이 엔티티를 'ScoreBlock'로 사용한다.
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

    # -----------------------------
    # 운영 점수
    # -----------------------------
    score = models.FloatField(null=True, blank=True)
    max_score = models.FloatField(null=True, blank=True)

    # 강사/운영 승인(통과 판단의 운영 입력값)
    teacher_approved = models.BooleanField(default=False)

    # 통과 여부(운영 표기용 스냅샷)
    # - 실제 차시 통과(SessionProgress.homework_passed)는 ProgressPolicy에 의해 결정됨
    passed = models.BooleanField(default=False)

    # -----------------------------
    # 편집 락
    # -----------------------------
    is_locked = models.BooleanField(default=False)
    lock_reason = models.CharField(
        max_length=30,
        choices=LockReason.choices,
        null=True,
        blank=True,
    )

    # 누가 마지막으로 수정했는지(프로젝트 User 모델 의존 방지)
    updated_by_user_id = models.PositiveIntegerField(null=True, blank=True)

    # meta 확장
    meta = models.JSONField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["enrollment_id", "session"],
                name="unique_homework_score_per_enrollment_session",
            )
        ]
        indexes = [
            models.Index(fields=["enrollment_id", "updated_at"]),
            models.Index(fields=["session", "updated_at"]),
        ]
        ordering = ["-updated_at", "-id"]

    def __str__(self) -> str:
        return f"HomeworkScore(enroll={self.enrollment_id}, session={self.session_id}, score={self.score})"

class HomeworkPolicy(TimestampModel):
    """
    Session 단위 과제 판정 정책

    - 시험 정책과 UX/개념 통일
    - homework score는 근사값이므로 percent 기준만 사용
    """

    session = models.OneToOneField(
        Session,
        on_delete=models.CASCADE,
        related_name="homework_policy",
    )

    # 통과 커트라인 (%)
    cutline_percent = models.PositiveSmallIntegerField(default=80)

    # 클리닉 연동 여부
    clinic_enabled = models.BooleanField(default=True)

    # 과제 불합격 시 클리닉 대상 여부
    clinic_on_fail = models.BooleanField(default=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"HomeworkPolicy(session={self.session_id}, cutline={self.cutline_percent}%)"
