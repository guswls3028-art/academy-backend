# PATH: apps/core/services/expense_policy.py
from __future__ import annotations


def normalize_expense_amount(amount) -> int:
    """
    Enterprise 정책:
    - amount는 항상 int >= 0 으로 정규화
    - 입력 실수(문자열/공백)를 안전하게 흡수
    """
    try:
        v = int(str(amount).strip())
        return v if v >= 0 else 0
    except Exception:
        return 0
