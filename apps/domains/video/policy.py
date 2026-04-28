from __future__ import annotations

from typing import Dict, Tuple, Optional


def evaluate_event_violation(
    *,
    event_type: str,
    policy: Dict,
    payload: Dict,
) -> Tuple[bool, Optional[str]]:
    """
    서버 기준 정책 위반 판정 (프론트 협조 없이)
    """
    if not policy:
        return False, None

    if event_type == "seek":
        if not policy.get("allow_skip", False):
            return True, "seek_not_allowed"

    if event_type == "speed":
        max_speed = policy.get("max_speed", 1.0)
        speed = float(payload.get("speed", 1.0))
        if speed > max_speed:
            return True, f"speed_exceeded:{speed}>{max_speed}"

    return False, None


def violation_should_revoke(*, violated_count: int, total: int) -> bool:
    """
    위반 누적 기준 (보수적으로 설정)
    """
    if violated_count >= 3:
        return True
    if total > 0 and (violated_count / total) >= 0.5:
        return True
    return False
