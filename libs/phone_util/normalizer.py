"""
전화번호 정규화 및 검증 모듈

한국 전화번호 형식:
- 휴대폰: 010, 011, 016, 017, 018, 019 (10-11자리)
- 지역번호: 02, 031, 032, 033, 041, 042, 043, 044, 051, 052, 053, 054, 055, 061, 062, 063, 064
"""

import re
from typing import Optional


class PhoneValidationError(ValueError):
    """전화번호 검증 오류"""
    pass


def normalize_phone(phone: Optional[str]) -> Optional[str]:
    """
    전화번호를 표준 형식으로 정규화
    
    Args:
        phone: 입력 전화번호 (다양한 형식 허용)
        
    Returns:
        정규화된 전화번호 (01012345678 형식) 또는 None
        
    Examples:
        >>> normalize_phone("010-1234-5678")
        '01012345678'
        >>> normalize_phone("+82 10-1234-5678")
        '01012345678'
        >>> normalize_phone("010 1234 5678")
        '01012345678'
        >>> normalize_phone(None)
        None
        >>> normalize_phone("")
        None
    """
    if not phone:
        return None
    
    # 문자열로 변환 및 공백 제거
    phone_str = str(phone).strip()
    if not phone_str:
        return None
    
    # 모든 하이픈, 공백, 괄호 제거
    phone_str = re.sub(r'[\s\-\(\)]', '', phone_str)
    
    # 국가코드 제거 (+82, 82로 시작하는 경우)
    if phone_str.startswith('+82'):
        phone_str = '0' + phone_str[3:]
    elif phone_str.startswith('82') and len(phone_str) >= 10:
        phone_str = '0' + phone_str[2:]
    
    # 숫자만 남기기
    phone_str = re.sub(r'[^\d]', '', phone_str)
    
    # 빈 문자열이면 None 반환
    if not phone_str:
        return None
    
    return phone_str


def validate_phone(phone: Optional[str], allow_empty: bool = False) -> str:
    """
    전화번호 검증 및 정규화
    
    Args:
        phone: 입력 전화번호
        allow_empty: 빈 값 허용 여부
        
    Returns:
        정규화된 전화번호
        
    Raises:
        PhoneValidationError: 유효하지 않은 전화번호인 경우
        
    Examples:
        >>> validate_phone("010-1234-5678")
        '01012345678'
        >>> validate_phone("01012345678")
        '01012345678'
        >>> validate_phone(None, allow_empty=True)
        ''
        >>> validate_phone("123")  # 너무 짧음
        Traceback (most recent call last):
        ...
        PhoneValidationError: Invalid phone number format
    """
    normalized = normalize_phone(phone)
    
    if not normalized:
        if allow_empty:
            return ""
        raise PhoneValidationError("Phone number is required")
    
    # 한국 전화번호 형식 검증
    # 휴대폰: 010, 011, 016, 017, 018, 019로 시작, 10-11자리
    mobile_pattern = r'^(010|011|016|017|018|019)\d{7,8}$'
    
    # 지역번호: 02(서울), 031~064
    landline_pattern = r'^(02|031|032|033|041|042|043|044|051|052|053|054|055|061|062|063|064)\d{7,8}$'
    
    if re.match(mobile_pattern, normalized) or re.match(landline_pattern, normalized):
        return normalized
    
    raise PhoneValidationError(f"Invalid phone number format: {phone}")
