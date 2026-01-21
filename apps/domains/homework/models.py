"""
Homework Domain Models

✅ 설계 고정(중요)
- homework 도메인은 "정의/정책" 레이어다.
- 런타임 결과(점수/락/스냅샷)는 homework_results 도메인 소유.

✅ homework 도메인의 책임
- Session 단위 과제 판정 정책(HomeworkPolicy)
  - 커트라인 (%)
  - 반올림 단위 (%)
  - 클리닉 연동 여부

✅ MVP 확정 요구사항 (2026-01-21)
1) 커트라인은 % 기반
2) 점수 입력 방식은 학원마다 다를 수 있음:
   - percent 직접 입력 (0~100)
   - raw 점수 입력 (예: 18/20)
   → percent 계산은 homework.utils에서 수행 가능(정책 기반)

⚠️ 중요:
- 운영 점수 스냅샷(HomeworkScore)은 homework_results로 이전됨.
"""

from __future__ import annotations

from django.db import models

from apps.api.common.models import TimestampModel
from apps.domains.lectures.models import Session


class HomeworkPolicy(TimestampModel):
    """
    Session 단위 과제 판정 정책

    ✅ 요구사항
    - 커트라인은 % 기반 (cutline_percent)
    - 점수는 score/max_score 기반으로 percent 계산 후 cutline 비교
    """

    session = models.OneToOneField(
        Session,
        on_delete=models.CASCADE,
        related_name="homework_policy",
    )

    # 통과 커트라인 (%)
    cutline_percent = models.PositiveSmallIntegerField(default=80)

    # 반올림 단위(%) - 예: 5면 83% → 85%로 반올림
    round_unit_percent = models.PositiveSmallIntegerField(default=5)

    # 클리닉 연동 여부
    clinic_enabled = models.BooleanField(default=True)

    # 과제 불합격 시 클리닉 대상 여부
    clinic_on_fail = models.BooleanField(default=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return (
            f"HomeworkPolicy(session={self.session_id}, "
            f"cutline={self.cutline_percent}%, unit={self.round_unit_percent}%)"
        )
