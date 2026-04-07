# PATH: apps/domains/exams/models/exam.py
from __future__ import annotations

from django.db import models
from django.db.models import Q
from apps.api.common.models import BaseModel
from apps.core.models.tenant import Tenant
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

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "초안"       # Legacy — 신규 생성 시 사용하지 않음
        OPEN = "OPEN", "진행중"
        CLOSED = "CLOSED", "마감"

    class AnswerVisibility(models.TextChoices):
        HIDDEN = "hidden", "비공개"
        AFTER_CLOSED = "after_closed", "마감 후 공개"
        ALWAYS = "always", "항상 공개"

    # 🔐 Tenant isolation — template exam도 세션 없이 tenant에 소속
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="exams",
        help_text="이 시험이 속한 학원. template exam의 tenant isolation 보장.",
    )

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    subject = models.CharField(max_length=100, blank=True, default="")

    exam_type = models.CharField(
        max_length=50,
        choices=ExamType.choices,
        default=ExamType.REGULAR,
    )
    is_active = models.BooleanField(default=True)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
        help_text="생성=OPEN(즉시 진행), 마감=CLOSED. DRAFT는 레거시(기존 데이터 호환).",
    )

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
    max_score = models.FloatField(
        default=100.0,
        help_text="만점. 답안등록 없이 합산 입력 시 사용. 답안등록 시 문항 합산으로 자동 재계산.",
    )
    display_order = models.PositiveIntegerField(
        default=0,
        help_text="성적탭 내 표시 순서 (작을수록 앞)",
    )

    open_at = models.DateTimeField(null=True, blank=True)
    close_at = models.DateTimeField(null=True, blank=True)

    answer_visibility = models.CharField(
        max_length=20,
        choices=AnswerVisibility.choices,
        default=AnswerVisibility.HIDDEN,
        help_text="정답 공개 정책: hidden=비공개, after_closed=마감 후 공개, always=항상 공개",
    )

    class Meta:
        db_table = "exams_exam"
        ordering = ["-created_at"]
        constraints = [
            # template는 template_exam을 가질 수 없음
            models.CheckConstraint(
                name="exams_exam_template_has_no_template_exam",
                condition=~Q(exam_type="template") | Q(template_exam__isnull=True),
            ),
            # P1-5: max_attempts >= 1 (0이면 시험 응시 불가)
            models.CheckConstraint(
                name="exams_exam_max_attempts_gte_1",
                condition=Q(max_attempts__gte=1),
            ),
            # P1-5: pass_score <= max_score (합격 불가능한 시험 방지)
            models.CheckConstraint(
                name="exams_exam_pass_lte_max",
                condition=Q(pass_score__lte=models.F("max_score")),
            ),
        ]

    def clean(self):
        """P1-5: 모델 레벨 유효성 검증"""
        from django.core.exceptions import ValidationError
        errors = {}
        if self.max_attempts is not None and self.max_attempts < 1:
            errors["max_attempts"] = "max_attempts는 1 이상이어야 합니다."
        if self.pass_score is not None and self.max_score is not None:
            if self.pass_score > self.max_score:
                errors["pass_score"] = (
                    f"합격 점수({self.pass_score})가 만점({self.max_score})을 초과할 수 없습니다."
                )
        if self.open_at and self.close_at and self.open_at >= self.close_at:
            errors["close_at"] = (
                f"마감 시각({self.close_at})이 시작 시각({self.open_at}) 이후여야 합니다."
            )
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return self.title

    @property
    def effective_template_exam_id(self) -> int:
        """
        ✅ 단일 진실 resolver
        - template이면 자기 자신
        - regular이면 template_exam 또는 없으면 자기 자신(설정 전)
        """
        if self.exam_type == self.ExamType.TEMPLATE:
            return int(self.id)
        if self.template_exam_id:
            return int(self.template_exam_id)
        return int(self.id)

    def assert_template(self):
        if self.exam_type != self.ExamType.TEMPLATE:
            raise ValueError("This operation is allowed only for template exams.")

    def assert_regular(self):
        if self.exam_type != self.ExamType.REGULAR:
            raise ValueError("This operation is allowed only for regular exams.")

    def should_show_answers(self) -> bool:
        """정답 공개 여부를 answer_visibility 정책에 따라 판단."""
        from django.utils import timezone

        if self.answer_visibility == self.AnswerVisibility.ALWAYS:
            return True
        if self.answer_visibility == self.AnswerVisibility.AFTER_CLOSED:
            if self.status == self.Status.CLOSED:
                return True
            if self.close_at and self.close_at <= timezone.now():
                return True
        return False
