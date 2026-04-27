"""
결과 상태 결정 (설계 REVIEW_REQUIRED 전략 반영)

Lite/Basic: 실패 없음 → SUCCESS + review_candidate 플래그. (단, 결정적 툴 제외)
Premium: confidence 구간에 따라 FAILED / REVIEW_REQUIRED / SUCCESS.

⚠️ Lite/Basic에서는 REVIEW_REQUIRED를 반환하지 않음 (항상 DONE + review_candidate만).
⚠️ 결정적 툴(파일 변환·집계·인덱싱)은 confidence 개념이 없음 → 실패는 항상 FAILED 노출.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from apps.domains.ai.services.runtime_flags import get_runtime_flag

logger = logging.getLogger(__name__)


# AI inference가 아닌 결정적 작업(파일 변환·집계·인덱싱). confidence 기반 review_candidate 의미 없음.
# Lite/Basic 정책에서 제외하고 실패는 그대로 FAILED 노출 (silent-timeout 방지).
_DETERMINISTIC_TOOL_JOB_TYPES = frozenset({
    "ppt_generation",
    "excel_parsing",
    "attendance_excel_export",
    "staff_excel_export",
    "matchup_index_exam",
    "matchup_manual_index",
})


def status_for_exception(tier: str, job_type: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    """
    예외/실패 시 최종 상태.

    - 결정적 툴(_DETERMINISTIC_TOOL_JOB_TYPES) → 항상 FAILED. 사용자 안내 필수.
    - AI inference + Lite/Basic → DONE + review_candidate (실패 없음 정책)
    - 그 외(Premium 등) → FAILED
    """
    if job_type and job_type.lower() in _DETERMINISTIC_TOOL_JOB_TYPES:
        return "FAILED", {}
    t = (tier or "basic").lower()
    if t in ("lite", "basic"):
        return "DONE", {"review_candidate": True, "from_exception": True}
    return "FAILED", {}


def determine_status(
    confidence: float,
    threshold_low: float = 0.5,
    threshold_high: float = 0.8,
    tier: str = "basic",
) -> Tuple[str, Dict[str, Any]]:
    """
    Lite/Basic은 FAIL 없이 SUCCESS + review_candidate만.
    Premium은 REVIEW_REQUIRED 노출 가능.

    Returns:
        (status, flags)
        - status: "DONE" | "FAILED" | "REVIEW_REQUIRED"
        - flags: {"review_candidate": bool, "confidence": float, ...}
    """
    tier = (tier or "basic").lower()
    shadow_mode = get_runtime_flag("ai_shadow_mode", default=True)

    if tier in ("lite", "basic"):
        # Lite/Basic: 실패 없음. 낮은 confidence도 DONE + 후보 플래그만
        if confidence < threshold_low:
            return "DONE", {"review_candidate": True, "confidence": confidence}
        if threshold_low <= confidence < threshold_high:
            return "DONE", {"review_candidate": True, "confidence": confidence}
        return "DONE", {"review_candidate": False, "confidence": confidence}

    # Premium: REVIEW_REQUIRED 노출 가능
    if confidence < threshold_low:
        return "FAILED", {"confidence": confidence}
    if threshold_low <= confidence < threshold_high:
        if shadow_mode:
            return "DONE", {"review_candidate": True, "confidence": confidence}
        return "REVIEW_REQUIRED", {"confidence": confidence}
    return "DONE", {"review_candidate": False, "confidence": confidence}
