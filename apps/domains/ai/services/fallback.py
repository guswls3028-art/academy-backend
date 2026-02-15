"""
GPU Fallback 로직

정책: Basic processing 실패도 Premium이면 Fallback 시도 (단, 비용 제어 조건 통과 시)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# 처리 실패 시 Fallback 대상 에러 코드 (error_code와 비교)
FALLBACK_ERROR_CODES = frozenset({
    "library_error",
    "corrupted_file",
    "timeout",
    "low_quality",
})


def _get_tenant_config(tenant_id: Optional[str]) -> Dict[str, Any]:
    """
    Tenant별 Fallback 설정 조회.
    TenantConfigModel 미구현 시 기본값 반환.
    """
    if not tenant_id:
        return {
            "has_premium_subscription": False,
            "allow_gpu_fallback": False,
            "gpu_fallback_threshold": 0.5,
        }
    try:
        from academy.adapters.db.django import repositories_ai as ai_repo
        config = ai_repo.get_tenant_config(tenant_id)
        if config:
            return {
                "has_premium_subscription": getattr(config, "has_premium_subscription", False),
                "allow_gpu_fallback": getattr(config, "allow_gpu_fallback", False),
                "gpu_fallback_threshold": getattr(config, "gpu_fallback_threshold", 0.5),
            }
    except Exception as e:
        logger.debug("TenantConfigModel not available: %s", e)
    return {
        "has_premium_subscription": False,
        "allow_gpu_fallback": False,
        "gpu_fallback_threshold": 0.5,
    }


def should_fallback_to_gpu(
    job: Any,
    error_type: Optional[str] = None,
    error_code: Optional[str] = None,
    result: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    GPU Fallback 여부 판단.

    Args:
        job: AIJob 또는 tenant_id를 가진 객체
        error_type: "validation_failed" | "processing_failed"
        error_code: 상세 에러 코드 (library_error, corrupted_file, timeout 등)
        result: 처리 결과 (confidence 등)

    Returns:
        Premium이고 비용 제어 조건 통과 시 True

    동작 정책:
        - error_type == "validation_failed" → 즉시 fallback
        - error_type == "processing_failed" 인 경우:
            - result["confidence"] <= threshold → fallback
            - error_code in FALLBACK_ERROR_CODES → fallback
        - Premium + allow_gpu_fallback 설정을 반드시 통과해야 fallback 허용
    """
    tenant_id = getattr(job, "tenant_id", None)
    config = _get_tenant_config(tenant_id)

    if not config["has_premium_subscription"]:
        return False
    if not config["allow_gpu_fallback"]:
        return False

    if error_type == "validation_failed":
        return True

    if error_type == "processing_failed":
        if result is not None:
            confidence = result.get("confidence", 1.0)
            if confidence <= config["gpu_fallback_threshold"]:
                return True
        if error_code is not None and error_code in FALLBACK_ERROR_CODES:
            return True

    return False
