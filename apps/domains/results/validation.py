from __future__ import annotations

import math
from typing import Any

from rest_framework.exceptions import ValidationError


def parse_finite_score(value: Any, *, field_name: str = "score") -> float:
    """Parse a score-like input without allowing booleans, NaN, or infinities."""
    if isinstance(value, bool):
        raise ValidationError(
            {"detail": f"{field_name} must be a finite number", "code": "INVALID"}
        )

    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValidationError(
            {"detail": f"{field_name} must be a finite number", "code": "INVALID"}
        )

    if not math.isfinite(parsed):
        raise ValidationError(
            {"detail": f"{field_name} must be a finite number", "code": "INVALID"}
        )
    return parsed
