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
    
    # Lite: OCR만 허용
    if tier == "lite":
        if job_type_lower not in ("ocr",):
            return False, f"Tier 'lite' only allows 'ocr' job type, got '{job_type}'"
        return True, None
    
    # Basic: OCR + OMR/status detection
    if tier == "basic":
        allowed_types = ("ocr", "omr_grading", "homework_video_analysis")
        if job_type_lower not in allowed_types:
            return False, f"Tier 'basic' only allows {allowed_types}, got '{job_type}'"
        return True, None
    
    # Premium: 모든 작업 허용
    if tier == "premium":
        return True, None
    
    return False, f"Unknown tier: {tier}"
