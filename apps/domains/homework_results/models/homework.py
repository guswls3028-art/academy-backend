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

from django.db import models

from apps.api.common.models import TimestampModel
from apps.domains.lectures.models import Session


class Homework(TimestampModel):
    """
    Session 단위 과제 엔티티 (또는 템플릿: session 없음)
    """

    class HomeworkType(models.TextChoices):
        TEMPLATE = "template", "템플릿"
        REGULAR = "regular", "일반"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "초안"
        OPEN = "OPEN", "진행중"
        CLOSED = "CLOSED", "마감"

    homework_type = models.CharField(
        max_length=20,
        choices=HomeworkType.choices,
        default=HomeworkType.REGULAR,
        db_index=True,
    )

    session = models.ForeignKey(
        Session,
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

    title = models.CharField(max_length=255)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )

    meta = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        indexes = [
            models.Index(fields=["session", "updated_at"]),
            models.Index(fields=["session", "status"]),
        ]

    def __str__(self) -> str:
        return f"Homework(id={self.id}, type={self.homework_type}, session={self.session_id}, title={self.title})"

    # =========================================================
    # ✅ 추가: SessionScores 메타용 대표 과제 제목 헬퍼
    # =========================================================
    @classmethod
    def get_representative_title_for_session(
        cls,
        *,
        session: Session,
        fallback: str = "과제",
    ) -> str:
        """
        SessionScores meta.homework.title 용

        규칙:
        1) 해당 세션의 Homework 중
           - 최신(updated_at desc)
           - CLOSED → OPEN → DRAFT 우선
        2) 없으면 fallback 반환

        ❗ 책임:
        - "어떤 과제를 대표로 보여줄지" 결정만 한다
        - 점수/정책/판정 ❌
        """

        qs = (
            cls.objects
            .filter(session=session)
            .order_by(
                models.Case(
                    models.When(status=cls.Status.CLOSED, then=0),
                    models.When(status=cls.Status.OPEN, then=1),
                    models.When(status=cls.Status.DRAFT, then=2),
                    default=3,
                    output_field=models.IntegerField(),
                ),
                "-updated_at",
                "-id",
            )
        )

        hw = qs.first()
        if hw and hw.title:
            return str(hw.title)

        return fallback
