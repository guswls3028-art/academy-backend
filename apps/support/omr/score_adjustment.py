from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


SCORE_ADJUSTMENT_KEY = "__score_adjustment__"
SCORE_UNIT = Decimal("0.1")


@dataclass(frozen=True)
class ScoreAdjustment:
    objective: float = 0.0
    subjective: float = 0.0

    @property
    def total(self) -> float:
        return round(self.objective + self.subjective, 1)

    def to_payload(self) -> dict[str, float]:
        payload: dict[str, float] = {}
        if self.objective > 0:
            payload["objective"] = self.objective
        if self.subjective > 0:
            payload["subjective"] = self.subjective
        return payload


def _non_negative_float(value: Any) -> float:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return 0.0
    if not parsed.is_finite() or parsed <= 0:
        return 0.0
    return float(parsed.quantize(SCORE_UNIT, rounding=ROUND_HALF_UP))


def normalize_score_adjustment_payload(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}

    objective = _non_negative_float(
        value.get("objective", value.get("choice", 0.0)),
    )
    subjective = _non_negative_float(
        value.get("subjective", value.get("essay", 0.0)),
    )
    return ScoreAdjustment(objective=objective, subjective=subjective).to_payload()


def get_score_adjustment_from_answers(answers: Any) -> ScoreAdjustment:
    if not isinstance(answers, dict):
        return ScoreAdjustment()
    payload = normalize_score_adjustment_payload(
        answers.get(SCORE_ADJUSTMENT_KEY),
    )
    return ScoreAdjustment(
        objective=payload.get("objective", 0.0),
        subjective=payload.get("subjective", 0.0),
    )
