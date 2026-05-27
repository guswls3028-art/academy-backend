"""
Tier별 처리 제한 강제

비즈니스 규칙:
- Lite: CPU OCR만 허용
- Basic: CPU 기반 OMR/status detection + 개선된 CPU OCR
- Premium: GPU 기반 전체 OCR + 고급 분석 (향후)
"""

from __future__ import annotations

import logging
from typing import Optional

from apps.domains.ai.job_types import BASIC_ALLOWED_JOB_TYPES, LITE_ALLOWED_JOB_TYPES

logger = logging.getLogger(__name__)


def enforce_tier_limits(
    *,
    tier: str,
    job_type: str,
) -> tuple[bool, Optional[str]]:
    """
    Tier별 처리 제한 강제
    
    Args:
        tier: Tier ("lite" | "basic" | "premium")
        job_type: 작업 타입
        
    Returns:
        tuple: (허용 여부, 에러 메시지)
    """
    tier = tier.lower()
    job_type_lower = job_type.lower()
    
    if tier == "lite":
        if job_type_lower not in LITE_ALLOWED_JOB_TYPES:
            return False, f"Tier 'lite' only allows {tuple(sorted(LITE_ALLOWED_JOB_TYPES))}, got '{job_type}'"
        return True, None

    if tier == "basic":
        if job_type_lower not in BASIC_ALLOWED_JOB_TYPES:
            return False, f"Tier 'basic' only allows {tuple(sorted(BASIC_ALLOWED_JOB_TYPES))}, got '{job_type}'"
        return True, None
    
    # Premium: 모든 작업 허용
    if tier == "premium":
        return True, None
    
    return False, f"Unknown tier: {tier}"
