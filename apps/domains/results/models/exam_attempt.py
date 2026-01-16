# apps/domains/results/models/exam_attempt.py
from django.db import models
from apps.api.common.models import BaseModel


class ExamAttempt(BaseModel):
    """
    학생의 '시험 1회 응시'를 나타내는 엔티티 (append-only)

    🔥 핵심 책임
    - Submission 단위가 아닌 '시험 응시 사실'의 고정
    - Result / ResultFact / Progress 집계의 기준점
    - 재시험/대표 attempt 교체의 단위

    ✅ 설계 고정 사항
    --------------------------------------------------
    1) ExamAttempt는 append-only 개념이다.
       - 기존 attempt를 수정하지 않는다.
       - 대표 attempt 변경은 is_representative 플래그로만 처리한다.

    2) Result / ResultItem은 항상
       "대표 attempt(is_representative=True)"를 가리키는 snapshot이다.

    3) meta 필드는 attempt 단위의 '운영/분석/재채점 근거'를 저장한다.
       - OMR 신뢰도
       - AI 판독 결과
       - total_score / pass_score 스냅샷
       - 재채점 사유 등
    """

    exam_id = models.PositiveIntegerField()
    enrollment_id = models.PositiveIntegerField()

    # Submission은 시도의 원인(event)
    submission_id = models.PositiveIntegerField(
        help_text="이 attempt를 발생시킨 submission"
    )

    # 1부터 시작 (시험 n번째 응시)
    attempt_index = models.PositiveIntegerField(help_text="1부터 시작")

    # 재시험 여부 (attempt_index > 1 과 의미적으로 동일하지만, 조회 최적화용)
    is_retake = models.BooleanField(default=False)

    # 서버가 판단하는 대표 attempt
    # Result는 항상 이 attempt를 기준으로 snapshot을 만든다.
    is_representative = models.BooleanField(default=True)

    status = models.CharField(
        max_length=20,
        choices=[
            ("pending", "Pending"),     # 생성됨
            ("grading", "Grading"),     # 채점 중
            ("done", "Done"),           # 채점 완료
            ("failed", "Failed"),       # 채점 실패
        ],
        default="pending",
    )

    # ==================================================
    # ✅ NEW: attempt 단위 메타데이터 (설계 필수)
    # ==================================================
    meta = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "Attempt 단위 메타데이터. "
            "OMR/AI 판독 정보, total_score, pass_score, "
            "재채점 근거 등 운영/분석용 정보 저장."
        ),
    )

    class Meta:
        db_table = "results_exam_attempt"
        unique_together = ("exam_id", "enrollment_id", "attempt_index")
        ordering = ["-created_at"]

    def __str__(self):
        return (
            f"ExamAttempt exam={self.exam_id} "
            f"enrollment={self.enrollment_id} "
            f"#{self.attempt_index}"
        )
