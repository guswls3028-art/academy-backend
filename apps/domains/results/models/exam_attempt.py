# apps/domains/results/models/exam_attempt.py
from django.db import models
from django.utils.functional import cached_property
from apps.api.common.models import BaseModel


class ExamAttemptQuerySet(models.QuerySet):
    def with_initial_grade(self):
        return self.annotate(is_initial_grade=models.Exists(
            self.model.objects.initial().filter(pk=models.OuterRef("pk")),
        ))

    def regular(self):
        """Clinic remediation is audit evidence, not an ordinary graded sitting."""
        return self.filter(clinic_link__isnull=True).filter(
            models.Q(meta__source__isnull=True) | ~models.Q(meta__source="clinic_remediation"),
        )

    def initial(self):
        first = self.model.objects.regular().filter(
            exam_id=models.OuterRef("exam_id"),
            enrollment_id=models.OuterRef("enrollment_id"),
        ).order_by("attempt_index", "id").values("id")[:1]
        return self.regular().filter(id=models.Subquery(first))


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
       - 기존 채점 사실은 수정하지 않는다. 확인된 OMR의 학생 오배정 정정은
         전용 서비스에서 원 귀속/순서를 감사 Fact에 보존한 뒤 현재 귀속을 바꾼다.
       - 대표 attempt 변경은 is_representative 플래그로만 처리한다.

    2) Result / ResultItem은 항상
       "대표 attempt(is_representative=True)"를 가리키는 snapshot이다.

    3) meta 필드는 attempt 단위의 '운영/분석/재채점 근거'를 저장한다.
       - OMR 신뢰도
       - AI 판독 결과
       - total_score / pass_score 스냅샷
       - 재채점 사유 등
    """

    exam = models.ForeignKey(
        "exams.Exam",
        on_delete=models.CASCADE,
        db_column="exam_id",
        related_name="attempts",
    )
    enrollment = models.ForeignKey(
        "enrollment.Enrollment",
        on_delete=models.CASCADE,
        db_column="enrollment_id",
        related_name="exam_attempts",
    )

    # Submission은 시도의 원인(event)
    submission_id = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="이 attempt를 발생시킨 submission (클리닉 직접 입력 시 null)"
    )

    # 1부터 시작 (시험 n번째 응시)
    attempt_index = models.PositiveIntegerField(help_text="1부터 시작")

    # 재시험 여부 (attempt_index > 1 과 의미적으로 동일하지만, 조회 최적화용)
    is_retake = models.BooleanField(default=False)

    # 서버가 판단하는 대표 attempt
    # Result는 항상 이 attempt를 기준으로 snapshot을 만든다.
    is_representative = models.BooleanField(default=True)

    # Facts at/before this immutable audit event belong to an earlier student
    # attribution. They remain readable for audit, never for current grading.
    current_fact_after_id = models.PositiveBigIntegerField(default=0, db_default=0)

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

    # ✅ V1.1.1: 클리닉 재시험 추적
    clinic_link = models.ForeignKey(
        "progress.ClinicLink",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="exam_retake_attempts",
        help_text="클리닉 재시험 시 연결된 ClinicLink (attempt_index>=2)",
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

    objects = ExamAttemptQuerySet.as_manager()

    @cached_property
    def is_initial_grade(self):
        if self.pk is None:
            return self.attempt_index == 1 and not self.clinic_link_id
        return type(self).objects.initial().filter(pk=self.pk).exists()

    class Meta:
        db_table = "results_exam_attempt"
        unique_together = ("exam", "enrollment", "attempt_index")
        ordering = ["-created_at"]
        constraints = [
            # P0-1: (exam, enrollment) 당 is_representative=True 최대 1개 보장
            # 장애/동시성에서 대표가 0개 또는 2개가 되는 경로 차단
            models.UniqueConstraint(
                fields=["exam", "enrollment"],
                condition=models.Q(is_representative=True),
                name="unique_representative_per_exam_enrollment",
            ),
            # P0-3: 동일 submission으로 중복 attempt 생성 차단 (동시성 안전)
            # submission_id=NULL(클리닉 직접 입력) 및 submission_id=0(수동 점수 입력)은 제외
            models.UniqueConstraint(
                fields=["submission_id"],
                condition=models.Q(submission_id__isnull=False) & ~models.Q(submission_id=0),
                name="unique_submission_per_attempt",
            ),
        ]

    def __str__(self):
        return (
            f"ExamAttempt exam={self.exam_id} "
            f"enrollment={self.enrollment_id} "
            f"#{self.attempt_index}"
        )
