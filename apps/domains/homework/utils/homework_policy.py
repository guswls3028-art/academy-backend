# PATH: apps/domains/homework/utils/homework_policy.py
# 역할: 점수 입력(%) 또는 (raw/max) → percent 계산 후 policy 기반 passed/clinic_required 결정

"""
Homework policy calculation utilities

✅ 책임
- percent 계산
- 반올림
- cutline 비교
- clinic_required 계산(정책 기반)

🚫 책임 아님
- progress 직접 갱신
"""

from __future__ import annotations
from typing import Optional, Tuple

from apps.domains.lectures.models import Session
from apps.domains.homework.models import HomeworkPolicy


def _round_percent(percent: float, unit: int) -> int:
    unit = int(unit or 1)
    if unit <= 0:
        unit = 1
    return int(round(percent / unit) * unit)


def calc_homework_percent(
    *,
    score: Optional[float],
    max_score: Optional[float],
) -> Optional[int]:
    """
    score/max_score -> percent 계산

    규칙:
    - score가 None -> None
    - max_score가 None -> score를 "percent 값"으로 간주 (0~100)
    - max_score가 0 -> None
    - percent = score/max_score*100
    """
    if score is None:
        return None

    if max_score is None:
        # percent 직접 입력 (예: 85)
        try:
            p = float(score)
        except Exception:
            return None
        return int(round(p))

    if max_score == 0:
        return None

    try:
        raw = (float(score) / float(max_score)) * 100.0
    except Exception:
        return None

    return int(round(raw))


def calc_homework_passed_and_clinic(
    *,
    session: Session,
    score: Optional[float],
    max_score: Optional[float],
) -> Tuple[bool, bool, Optional[int]]:
    """
    Homework 합불 + 클리닉 계산 (HomeworkPolicy 단일 진실)

    반환:
    - passed: bool
    - clinic_required: bool
    - percent: Optional[int] (rounded percent, COUNT 모드일 때는 None)
    """
    policy, _ = HomeworkPolicy.objects.get_or_create(
        session=session,
        defaults={
            "cutline_percent": 80,
            "round_unit_percent": 5,
            "clinic_enabled": True,
            "clinic_on_fail": True,
        },
    )

    mode = getattr(policy, "cutline_mode", None) or "PERCENT"
    cutline_value = int(getattr(policy, "cutline_value", 0) or policy.cutline_percent or 80)

    if mode == "COUNT":
        # 문항 수 기준: score >= cutline_value 이면 합격 (score는 정답 수/점수로 해석)
        if score is None:
            return False, False, None
        passed = bool(float(score) >= cutline_value)
        clinic_required = bool(
            policy.clinic_enabled and policy.clinic_on_fail and (not passed)
        )
        percent = calc_homework_percent(score=score, max_score=max_score)
        rounded = _round_percent(percent, policy.round_unit_percent) if percent is not None else None
        return passed, clinic_required, rounded
    else:
        # 퍼센트 기준 (기존 로직)
        percent = calc_homework_percent(score=score, max_score=max_score)
        if percent is None:
            return False, False, None
        rounded = _round_percent(percent, policy.round_unit_percent)
        threshold = int(cutline_value if cutline_value else policy.cutline_percent or 0)
        passed = bool(rounded >= threshold)
        clinic_required = bool(
            policy.clinic_enabled and policy.clinic_on_fail and (not passed)
        )
        return passed, clinic_required, rounded
