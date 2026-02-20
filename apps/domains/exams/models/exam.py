# PATH: apps/domains/exams/models/exam.py
from __future__ import annotations

from django.db import models
from django.db.models import Q
from apps.api.common.models import BaseModel
from apps.domains.lectures.models import Session


class Exam(BaseModel):
    """
    Exam

    ✅ 확정 정책
    - exam_type="template": 양식 전용 (응시/제출/결과/대상자 ❌)
    - exam_type="regular" : 운영 시험 (반드시 template_exam 기반)

    ✅ 핵심
    - 시험의 정체성은 exam.id
    - 같은 시험(regular)을 여러 세션에 붙여 재사용 가능 (N:M)
    - Sheet/Question/AnswerKey/Asset는 template_exam이 단일 진실
    """

    class ExamType(models.TextChoices):
        TEMPLATE = "template", "Template"
        REGULAR = "regular", "Regular"

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    subject = models.CharField(max_length=100, blank=True, default="")

    exam_type = models.CharField(
        max_length=50,
        choices=ExamType.choices,
        default=ExamType.REGULAR,
    )
    is_active = models.BooleanField(default=True)

    # ===============================
    # 🔥 Session : Exam = N:M
    # ===============================
    sessions = models.ManyToManyField(
        Session,
        related_name="exams",
        blank=True,
        help_text="이 시험이 속한 차시들",
    )

    # ===============================
    # 🔥 Template binding (단일 진실)
    # ===============================
    template_exam = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="derived_exams",
        help_text="regular 시험이 참조하는 template 시험",
    )

    allow_retake = models.BooleanField(default=False)
    max_attempts = models.PositiveIntegerField(default=1)
    pass_score = models.FloatField(default=0.0)

    open_at = models.DateTimeField(null=True, blank=True)
    close_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "exams_exam"
        ordering = ["-created_at"]
        constraints = [
            # template는 template_exam을 가질 수 없음
            models.CheckConstraint(
                name="exams_exam_template_has_no_template_exam",
                check=~Q(exam_type="template") | Q(template_exam__isnull=True),
            ),
            # regular은 template_exam이 반드시 필요
            models.CheckConstraint(
                name="exams_exam_regular_requires_template_exam",
                check=~Q(exam_type="regular") | Q(template_exam__isnull=False),
            ),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def effective_template_exam_id(self) -> int:
        """
        ✅ 단일 진실 resolver
        - template이면 자기 자신
        - regular이면 template_exam
        """
        if self.exam_type == self.ExamType.TEMPLATE:
            return int(self.id)
        return int(self.template_exam_id)

    def assert_template(self):
        if self.exam_type != self.ExamType.TEMPLATE:
            raise ValueError("This operation is allowed only for template exams.")

    def assert_regular(self):
        if self.exam_type != self.ExamType.REGULAR:
            raise ValueError("This operation is allowed only for regular exams.")
