"""
전역 런타임 플래그 (배포 없이 ON/OFF).

Shadow Mode 등 운영 중 즉시 변경 가능한 설정.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 메모리 캐시 (선택): DB 부하 감소, TTL 없이 프로세스 내 일관성만
_runtime_flag_cache: dict[str, Optional[str]] = {}


def get_runtime_flag(key: str, default: bool = False) -> bool:
    """
    DB(ai_runtime_config) 기반 플래그 조회.
    운영 중 즉시 ON/OFF 가능.

    Args:
        key: 예) "ai_shadow_mode"
        default: 레코드 없을 때 기본값

    Returns:
        value가 "1", "true", "yes" (대소문자 무관)이면 True
    """
    global _runtime_flag_cache
    if key in _runtime_flag_cache:
        raw = _runtime_flag_cache[key]
        return _parse_bool(raw, default)

    try:
        from apps.domains.ai.models import AIRuntimeConfigModel
        row = AIRuntimeConfigModel.objects.filter(key=key).first()
        if row is None:
            _runtime_flag_cache[key] = None
            return default
        _runtime_flag_cache[key] = row.value
        return _parse_bool(row.value, default)
    except Exception as e:
        logger.debug("get_runtime_flag %s: %s", key, e)
        return default


def _parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes")


def clear_runtime_flag_cache(key: Optional[str] = None) -> None:
    """캐시 무효화 (설정 변경 후 호출 권장)."""
    global _runtime_flag_cache
    if key is None:
        _runtime_flag_cache.clear()
    else:
        _runtime_flag_cache.pop(key, None)
