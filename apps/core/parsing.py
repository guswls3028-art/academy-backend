from __future__ import annotations

from rest_framework.exceptions import ValidationError


TRUE_VALUES = {"1", "true", "t", "yes", "y", "on"}
FALSE_VALUES = {"0", "false", "f", "no", "n", "off"}


def parse_bool(value, *, field_name: str = "value") -> bool:
    """
    Parse request boolean safely.

    Accepts bool/int/str and rejects ambiguous values with 400-friendly error.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if value in (0, 1):
            return bool(value)
        raise ValidationError({field_name: "0 또는 1만 허용됩니다."})
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in TRUE_VALUES:
            return True
        if normalized in FALSE_VALUES:
            return False
        raise ValidationError({field_name: "boolean 값(true/false)만 허용됩니다."})
    raise ValidationError({field_name: "boolean 값이 필요합니다."})
