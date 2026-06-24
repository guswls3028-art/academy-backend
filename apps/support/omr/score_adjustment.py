from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any


SCORE_ADJUSTMENT_KEY = "__score_adjustment__"


@dataclass(frozen=True)
class ScoreAdjustment:
    objective: float = 0.0
    subjective: float = 0.0

    @property
    def total(self) -> float:
        return round(self.objective + self.subjective, 2)

    def to_payload(self) -> dict[str, float]:
        payload: dict[str, float] = {}
        if self.objective > 0:
            payload["objective"] = self.objective
        if self.subjective > 0:
            payload["subjective"] = self.subjective
        return payload


def _non_negative_float(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not isfinite(parsed) or parsed <= 0:
        return 0.0
    return round(parsed, 2)


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
