# PATH: apps/domains/homework_results/models/homework.py
"""
Homework Entity (Runtime / Operational)

✅ 목적
- "과제 목록/상세"를 제공하기 위한 실체 엔티티
- 프론트 좌측 패널(시험/과제 리스트)에서 사용
- HomeworkPolicy(세션 1:1 정책)과는 별개로,
  실제 "과제"는 세션 내 여러 개가 존재할 수 있다.

✅ 설계 기준
- 시험(Exam)처럼 Session 1:N 구조 지원
- HomeworkScore는 Enrollment x Session 스냅샷이지만,
  Homework 자체는 "과제 항목" 메타 정보다.

⚠️ 주의
- 기존 homework 도메인은 정책만 유지한다.
- 여기서는 "운영/리소스 엔티티"만 만든다.
"""

from __future__ import annotations

from django.db import models

from apps.api.common.models import TimestampModel
from apps.domains.lectures.models import Session


class Homework(TimestampModel):
    """
    Session 단위 과제 엔티티

    최소 필드:
    - session: 소속 세션
    - title: 제목
    - status: 상태 (DRAFT/OPEN/CLOSED)
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "초안"
        OPEN = "OPEN", "진행중"
        CLOSED = "CLOSED", "마감"

    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        related_name="homeworks",
        db_index=True,
    )

    title = models.CharField(max_length=255)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )

    # 확장용 (추후: 제출 방식/마감일/공지 등)
    meta = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        indexes = [
            models.Index(fields=["session", "updated_at"]),
            models.Index(fields=["session", "status"]),
        ]

    def __str__(self) -> str:
        return f"Homework(id={self.id}, session={self.session_id}, title={self.title})"
