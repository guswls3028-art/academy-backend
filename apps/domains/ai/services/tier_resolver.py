"""
Tier 결정 로직

비즈니스 규칙:
- Lite: CPU OCR만 허용 (복지 티어)
- Basic: CPU 기반 OMR/status detection + 개선된 CPU OCR
- Premium: GPU 기반 전체 OCR + 고급 분석 (향후)
"""

from __future__ import annotations

import logging
from typing import Optional

from django.conf import settings

logger = logging.getLogger(__name__)


def resolve_tier(
    *,
    tenant_id: Optional[str] = None,
    job_type: str,
    payload: dict,
) -> str:
    """
    작업의 Tier 결정
    
    Args:
        tenant_id: Tenant ID (선택사항, tenant별 tier 설정 가능)
        job_type: 작업 타입 (예: "ocr", "omr_grading")
        payload: 작업 페이로드
        
    Returns:
        str: "lite" | "basic" | "premium"
    """
    # 1. 명시적 tier 지정 (payload에서)
    explicit_tier = payload.get("tier")
    if explicit_tier in ("lite", "basic", "premium"):
        return explicit_tier.lower()
    
    # 2. Tenant별 tier 설정 (향후 확장 가능)
    # tenant_tier = get_tenant_tier(tenant_id) if tenant_id else None
    # if tenant_tier:
    #     return tenant_tier
    
    # 3. 작업 타입 기반 기본 tier 결정
    job_type_lower = job_type.lower()
    
    # OCR만 필요한 작업 -> Lite 가능
    if job_type_lower in ("ocr",):
        # 기본값: basic (향후 tenant 설정으로 lite 가능)
        return "basic"
    
    # OMR/status detection, 엑셀 파싱 -> Basic 이상 필요
    if job_type_lower in ("omr_grading", "homework_video_analysis", "excel_parsing"):
        return "basic"
    
    # 고급 분석 -> Premium 필요 (향후)
    if job_type_lower in ("advanced_analysis", "full_ocr"):
        return "premium"
    
    # 기본값: basic
    return "basic"


def validate_tier_for_job_type(tier: str, job_type: str) -> bool:
    """
    Tier와 작업 타입의 호환성 검증
    
    Args:
        tier: Tier ("lite" | "basic" | "premium")
        job_type: 작업 타입
        
    Returns:
        bool: 호환 가능 여부
    """
    tier = tier.lower()
    job_type_lower = job_type.lower()
    
    # Lite: OCR만 허용
    if tier == "lite":
        return job_type_lower in ("ocr",)
    
    # Basic: OCR + OMR/status detection + 엑셀 파싱/내보내기
    if tier == "basic":
        return job_type_lower in (
            "ocr",
            "omr_grading",
            "homework_video_analysis",
            "excel_parsing",
            "attendance_excel_export",
            "staff_excel_export",
        )
    
    # Premium: 모든 작업 허용
    if tier == "premium":
        return True
    
    return False
