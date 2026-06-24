# PATH: apps/core/services/expense_policy.py
from __future__ import annotations


def normalize_expense_amount(amount) -> int:
    """
    Enterprise 정책:
    - amount는 항상 int > 0 으로 정규화
    - 0/음수/공백/문자 입력은 지출 데이터 무결성을 위해 거부
    """
    try:
        v = int(str(amount).strip())
    except (TypeError, ValueError):
        raise ValueError("amount must be a positive integer") from None
    if v <= 0:
        raise ValueError("amount must be a positive integer")
    return v
