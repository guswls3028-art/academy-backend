# PATH: apps/domains/homework_results/models/homework.py
"""
Homework Entity (Runtime / Operational)

✅ 목적
- "과제 목록/상세"를 제공하기 위한 실체 엔티티
- 프론트 좌측 패널(시험/과제 리스트)에서 사용
- HomeworkPolicy(세션 1:1 정책)과는 별개로,
  실제 "과제"는 세션 내 여러 개가 존재할 수 있다.

✅ 템플릿 지원 (시험과 동일)
- homework_type=template: 양식 전용 (세션 없음)
- homework_type=regular: 운영 과제 (session 필수, template_homework 선택)
- 다른 강의에서 동일 과제 불러오기·통계 합산 가능
"""

from __future__ import annotations

from math import isfinite
from typing import Any

from django.db import models

from apps.api.common.models import TimestampModel


class Homework(TimestampModel):
    """
    Session 단위 과제 엔티티 (또는 템플릿: session 없음)
    """

    class HomeworkType(models.TextChoices):
        TEMPLATE = "template", "템플릿"
        REGULAR = "regular", "일반"

    class CutlineMode(models.TextChoices):
        PERCENT = "PERCENT", "퍼센트 (%)"
        COUNT = "COUNT", "점수"

    class GradingMode(models.TextChoices):
        SCORE = "SCORE", "점수형"
        COMPLETION = "COMPLETION", "완료형"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "초안"       # Legacy — 신규 생성 시 사용하지 않음
        OPEN = "OPEN", "진행중"
        CLOSED = "CLOSED", "마감"

    DEFAULT_MAX_SCORE = 100.0

    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        related_name="homeworks",
        help_text="이 과제가 속한 학원.",
    )

    homework_type = models.CharField(
        max_length=20,
        choices=HomeworkType.choices,
        default=HomeworkType.REGULAR,
        db_index=True,
    )

    session = models.ForeignKey(
        "lectures.Session",
        on_delete=models.CASCADE,
        related_name="homeworks",
        db_index=True,
        null=True,
        blank=True,
        help_text="일반(regular) 과제는 필수. 템플릿은 null.",
    )

    template_homework = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="derived_homeworks",
        help_text="일반 과제가 참조하는 템플릿",
    )

    source_exam = models.OneToOneField(
        "exams.Exam",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="source_homework",
        help_text=(
            "워크북 문항·해설 원본을 보관하는 비노출 regular Exam. "
            "시험 분리·검수 엔진을 과제에서도 동일하게 사용한다."
        ),
    )

    title = models.CharField(max_length=255)

    grading_mode = models.CharField(
        max_length=20,
        choices=GradingMode.choices,
        default=GradingMode.SCORE,
        help_text="SCORE는 수치 점수, COMPLETION은 완료/미완료(1/0)로 기록한다.",
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
        help_text="Legacy compatibility storage. Business logic uses homework_type + session + removed_from_session_at.",
    )

    meta = models.JSONField(null=True, blank=True)
    cutline_mode = models.CharField(
        max_length=10,
        choices=CutlineMode.choices,
        null=True,
        blank=True,
        help_text="비어 있으면 차시 공통 과제 정책을 사용한다.",
    )
    cutline_value = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="PERCENT: 0~100, COUNT: 이 과제의 원점수 커트라인.",
    )
    round_unit_percent = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="과제별 퍼센트 반올림 단위. 비어 있으면 차시 정책을 사용한다.",
    )
    display_order = models.PositiveIntegerField(
        default=0,
        help_text="성적탭 내 표시 순서 (작을수록 앞)",
    )

    class Meta:
        ordering = ["-updated_at", "-id"]
        indexes = [
            models.Index(fields=["session", "updated_at"]),
        ]

    def __str__(self) -> str:
        return f"Homework(id={self.id}, type={self.homework_type}, session={self.session_id}, title={self.title})"

    @staticmethod
    def max_score_from_meta(meta: Any) -> float:
        if not isinstance(meta, dict):
            return Homework.DEFAULT_MAX_SCORE
        try:
            value = float(meta.get("default_max_score"))
        except (TypeError, ValueError, OverflowError):
            return Homework.DEFAULT_MAX_SCORE
        return value if isfinite(value) and value > 0 else Homework.DEFAULT_MAX_SCORE

    @property
    def default_max_score(self) -> float:
        """과제 점수·표시·정책 계산이 함께 사용하는 과제별 만점."""
        if self.grading_mode == self.GradingMode.COMPLETION:
            return 1.0
        return self.max_score_from_meta(self.meta)

    # =========================================================
    # ✅ 추가: SessionScores 메타용 대표 과제 제목 헬퍼
    # =========================================================
    @classmethod
    def get_representative_title_for_session(
        cls,
        *,
        session: Any,
        fallback: str = "과제",
    ) -> str:
        """
        SessionScores meta.homework.title 용

        규칙:
        1) 해당 세션의 Homework 중
           - 최신(updated_at desc)
           - removed_from_session_at 없는 live 과제 우선
        2) 없으면 fallback 반환

        ❗ 책임:
        - "어떤 과제를 대표로 보여줄지" 결정만 한다
        - 점수/정책/판정 ❌
        """
        qs = (
            cls.objects
            .filter(session=session)
            .exclude(meta__removed_from_session_at__isnull=False)
            .order_by(
                "-updated_at",
                "-id",
            )
        )
        hw = qs.first()
        if hw and hw.title:
            return str(hw.title)
        return fallback
