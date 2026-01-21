"""
Homework Results Domain Models

✅ 핵심 설계 (중요 / 레이어 고정)
- homework_results 도메인은 "런타임 결과(스냅샷)"만 소유한다.
- Homework 정의/정책은 homework 도메인 소유.
- clinic 판단/차시 통과/집계는 progress 도메인 소유.

✅ 이 모델의 역할
- Enrollment x Session 단위 Homework 결과 스냅샷(운영 입력 포함)
- lock 상태, 운영 승인 여부, 점수(원점수/percent 모두)를 저장
- SessionScores API는 이 엔티티를 'ScoreBlock'로 사용한다.

🚫 이 모델이 하지 않는 것
- 제출/원본/상태: submissions 도메인
- 시험 채점/결과: results 도메인
- 차시 통과/집계: progress 도메인

⚠️ DB 호환성 (중요)
- 기존 homework 도메인의 HomeworkScore 테이블을 그대로 재사용한다.
- db_table = "homework_homeworkscore" 고정
- 따라서 DB DROP/CREATE 없이 "앱 소유권"만 이전한다.
"""

from __future__ import annotations

from django.db import models

from apps.api.common.models import TimestampModel
from apps.domains.lectures.models import Session


class HomeworkScore(TimestampModel):
    """
    Enrollment x Session 단위 숙제 점수/승인 스냅샷

    DESIGN:
    - 이 값은 progress 계산에 직접 사용되기보다는,
      progress pipeline이 읽는 Submission.homework_* 를 갱신하기 위한 운영 입력/결과 스냅샷이다.
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
    # 점수 입력 방식은 학원마다 다를 수 있음:
    # - percent 입력: score=85, max_score=100
    # - raw 입력: score=18, max_score=20
    score = models.FloatField(null=True, blank=True)
    max_score = models.FloatField(null=True, blank=True)

    # 강사/운영 승인(통과 판단의 운영 입력값)
    teacher_approved = models.BooleanField(default=False)

    # 통과 여부(운영 표기용 스냅샷)
    # - 실제 차시 통과(SessionProgress.homework_passed)는 ProgressPolicy에 의해 결정됨
    passed = models.BooleanField(default=False)

    # 클리닉 대상 여부 (scores 탭에서 바로 표현하기 위한 운영 스냅샷)
    clinic_required = models.BooleanField(default=False)

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
        # ✅ DB 재사용 (중요)
        db_table = "homework_homeworkscore"

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
        return (
            f"HomeworkScore(enroll={self.enrollment_id}, "
            f"session={self.session_id}, score={self.score}, max={self.max_score})"
        )
