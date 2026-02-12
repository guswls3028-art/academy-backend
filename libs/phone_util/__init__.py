"""
전화번호 정규화 및 검증 유틸리티

한국 전화번호 표준화:
- 입력: 010-1234-5678, 01012345678, +82 10-1234-5678 등
- 출력: 01012345678 (하이픈 제거, 국가코드 제거)
"""

from .normalizer import normalize_phone, validate_phone, PhoneValidationError

__all__ = ["normalize_phone", "validate_phone", "PhoneValidationError"]
