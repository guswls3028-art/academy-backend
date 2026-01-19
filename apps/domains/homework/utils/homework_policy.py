# PATH: apps/domains/homework/utils/homework_policy.py
"""
Homework policy calculation utilities

✅ 책임
- percent 계산
- 반올림
- cutline 비교

🚫 책임 아님
- clinic 판단
- progress 직접 갱신
"""

from __future__ import annotations
from typing import Optional

from apps.domains.progress.models import ProgressPolicy
from apps.domains.lectures.models import Session


def calc_homework_passed(
    *,
    session: Session,
    score: Optional[float],
    max_score: Optional[float],
) -> bool:
    """
    Homework 합불 계산 (policy 기반)

    규칙:
    - score/max_score 중 하나라도 None → False
    - percent = score / max * 100
    - round_unit 단위 반올림
    - cutline 이상이면 passed
    """
    if score is None or max_score in (None, 0):
        return False

    policy = (
        ProgressPolicy.objects
        .filter(lecture=session.lecture)
        .order_by("-id")
        .first()
    )

    cutline = int(getattr(policy, "homework_cutline_percent", 80))
    unit = int(getattr(policy, "homework_round_unit", 5)) or 1

    raw_percent = (float(score) / float(max_score)) * 100
    rounded = int(round(raw_percent / unit) * unit)

    return bool(rounded >= cutline)
