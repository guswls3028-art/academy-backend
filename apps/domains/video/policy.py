from __future__ import annotations

import math
from typing import Dict, Tuple, Optional

VIDEO_COMPLETION_THRESHOLD = 0.9
MIN_VIDEO_MAX_SPEED = 0.25
MAX_VIDEO_MAX_SPEED = 5.0
VIDEO_FORWARD_SKIP_RATIO = 0.2
VIDEO_FORWARD_SKIP_MAX_SECONDS = 30 * 60
VIDEO_FORWARD_SKIP_STEP_SECONDS = 10


def video_forward_skip_budget(*, duration: object, used_seconds: object = 0) -> Dict[str, int | str | bool]:
    """Return the server-owned forward-skip allowance for one video progress row."""
    try:
        total_seconds = int(duration or 0)
    except (TypeError, ValueError):
        total_seconds = 0

    try:
        consumed_seconds = int(used_seconds or 0)
    except (TypeError, ValueError):
        consumed_seconds = 0

    total_seconds = max(0, total_seconds)
    consumed_seconds = max(0, consumed_seconds)
    limit_seconds = min(
        int(total_seconds * VIDEO_FORWARD_SKIP_RATIO),
        VIDEO_FORWARD_SKIP_MAX_SECONDS,
    )
    consumed_seconds = min(consumed_seconds, limit_seconds)
    remaining_seconds = max(0, limit_seconds - consumed_seconds)

    if total_seconds <= 0:
        unavailable_reason = "duration_unavailable"
    elif remaining_seconds <= 0:
        unavailable_reason = "limit_reached"
    else:
        unavailable_reason = ""

    return {
        "enabled": total_seconds > 0 and remaining_seconds > 0,
        "step_seconds": VIDEO_FORWARD_SKIP_STEP_SECONDS,
        "ratio_percent": int(VIDEO_FORWARD_SKIP_RATIO * 100),
        "max_seconds": VIDEO_FORWARD_SKIP_MAX_SECONDS,
        "limit_seconds": limit_seconds,
        "used_seconds": consumed_seconds,
        "remaining_seconds": remaining_seconds,
        "unavailable_reason": unavailable_reason,
    }


def normalize_video_max_speed(value: object) -> float:
    try:
        speed = float(value)
    except (TypeError, ValueError):
        raise ValueError("max_speed must be a number") from None
    if not math.isfinite(speed):
        raise ValueError("max_speed must be finite")
    if speed < MIN_VIDEO_MAX_SPEED or speed > MAX_VIDEO_MAX_SPEED:
        raise ValueError(f"max_speed must be between {MIN_VIDEO_MAX_SPEED} and {MAX_VIDEO_MAX_SPEED}")
    return speed


def normalize_video_progress(progress: object) -> float:
    try:
        value = float(progress or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(value):
        return 0.0
    if value > 1:
        value = value / 100
    return max(0.0, min(1.0, value))


def is_video_progress_complete(progress: object, completed: bool = False) -> bool:
    return bool(completed) or normalize_video_progress(progress) >= VIDEO_COMPLETION_THRESHOLD


def crossed_video_completion_threshold(previous: object, current: object) -> bool:
    return (
        normalize_video_progress(previous) < VIDEO_COMPLETION_THRESHOLD
        <= normalize_video_progress(current)
    )


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
