from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.db import TenantQuerySet
from apps.core.models.base import TimestampModel


class StudentReportedScore(TimestampModel):
    """학생이 제출한 학교·모의고사 과목 성적."""

    class Source(models.TextChoices):
        SCHOOL_EXAM = "school_exam", "학교 지필평가"
        NATIONAL_MOCK = "national_mock", "교육청 전국연합학력평가"
        KICE_MOCK = "kice_mock", "평가원 수능 모의평가"

    class ExamRound(models.TextChoices):
        FIRST = "first", "1차 지필평가(중간고사)"
        SECOND = "second", "2차 지필평가(기말고사)"
        PERFORMANCE = "performance", "수행평가"
        OTHER = "other", "기타 학교 평가"

    class Status(models.TextChoices):
        PENDING = "pending", "검토 대기"
        VERIFIED = "verified", "확인 완료"
        REJECTED = "rejected", "반려"
        VOIDED = "voided", "통계 제외"
        SELF_ENTERED = "self_entered", "직접 입력"
        SELF_REMOVED = "self_removed", "직접 입력 수정으로 제외"

    class GradeScale(models.TextChoices):
        FIVE = "five", "5등급제"
        NINE = "nine", "9등급제"

    objects = TenantQuerySet.as_manager()

    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        related_name="student_reported_scores",
        db_index=True,
    )
    student = models.ForeignKey(
        "students.Student",
        on_delete=models.CASCADE,
        related_name="reported_scores",
        db_index=True,
    )
    evidence_file = models.ForeignKey(
        "inventory.InventoryFile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="student_reported_scores",
    )
    direct_entry = models.BooleanField(default=False, db_default=False)
    submitted_by = models.ForeignKey(
        "core.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_student_scores",
    )

    source = models.CharField(max_length=24, choices=Source.choices, db_index=True)
    academic_year = models.PositiveSmallIntegerField(db_index=True)
    semester = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(2)],
    )
    exam_round = models.CharField(
        max_length=12,
        choices=ExamRound.choices,
        blank=True,
        default="",
    )
    exam_name = models.CharField(
        max_length=80,
        blank=True,
        default="",
        help_text="기타 학교 평가일 때 성적표에 기재된 시험명",
    )
    exam_month = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(12)],
    )
    exam_date = models.DateField(null=True, blank=True)
    subject = models.CharField(max_length=50)

    score = models.DecimalField(max_digits=7, decimal_places=2)
    max_score = models.DecimalField(max_digits=7, decimal_places=2)
    standard_score = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    percentile = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    grade_rank = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(9)],
        help_text="학교 또는 모의고사 성적표에 표시된 등급",
    )
    grade_scale = models.CharField(
        max_length=8,
        choices=GradeScale.choices,
        blank=True,
        default="",
    )
    achievement_level = models.CharField(
        max_length=1,
        blank=True,
        default="",
        help_text="학교 성취도(A~E)",
    )
    subject_average = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    standard_deviation = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    cohort_size = models.PositiveIntegerField(null=True, blank=True)

    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    reviewed_by = models.ForeignKey(
        "core.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_student_scores",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        db_table = "results_student_reported_score"
        ordering = ["-academic_year", "-exam_date", "-created_at"]
        indexes = [
            models.Index(fields=["tenant", "status", "-created_at"]),
            models.Index(fields=["tenant", "student", "source", "-academic_year"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(evidence_file__isnull=False)
                    | models.Q(status__in=["rejected", "voided"])
                    | models.Q(
                        direct_entry=True,
                        status__in=["self_entered", "self_removed"],
                        source="school_exam",
                        semester__isnull=False,
                        semester__in=[1, 2],
                        exam_round__in=["first", "second"],
                    )
                ),
                name="reported_score_active_evidence_or_direct",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(direct_entry=False) & ~models.Q(status__in=["self_entered", "self_removed"])
                    | models.Q(
                        direct_entry=True,
                        evidence_file__isnull=True,
                        status__in=["self_entered", "self_removed"],
                    )
                ),
                name="reported_score_direct_entry_valid",
            ),
        ]

    def __str__(self):
        return f"StudentReportedScore(tenant={self.tenant_id}, student={self.student_id}, source={self.source})"
