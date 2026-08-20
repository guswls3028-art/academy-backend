from __future__ import annotations

from collections.abc import Mapping

from rest_framework.exceptions import ValidationError


def parse_query_bool(
    query_params: Mapping,
    name: str,
    *,
    default: bool | None = None,
) -> bool | None:
    """Parse a boolean query parameter without treating typos as false."""
    raw = query_params.get(name)
    if raw in (None, ""):
        return default
    normalized = str(raw).strip().lower()
    if normalized in {"1", "true"}:
        return True
    if normalized in {"0", "false"}:
        return False
    raise ValidationError({name: "true 또는 false 값을 입력해 주세요."})


def parse_query_int(
    query_params: Mapping,
    name: str,
    *,
    default: int | None = None,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int | None:
    """Parse one integer query parameter and fail with a stable HTTP 400."""
    raw = query_params.get(name)
    if raw in (None, ""):
        parsed = default
    else:
        try:
            parsed = int(raw)
        except (TypeError, ValueError, OverflowError):
            parsed = None
            invalid = True
        else:
            invalid = False

        if invalid:
            raise ValidationError({name: "정수 값을 입력해 주세요."})

    if parsed is None:
        return None
    if min_value is not None and parsed < min_value:
        raise ValidationError({name: f"{min_value} 이상이어야 합니다."})
    if max_value is not None and parsed > max_value:
        raise ValidationError({name: f"{max_value} 이하여야 합니다."})
    return parsed
