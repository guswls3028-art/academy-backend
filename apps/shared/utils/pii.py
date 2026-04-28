"""PII 마스킹 유틸 — 외부 API(OpenAI/Vision/Solapi 로깅) 호출 시 학생/학부모 정보 보호."""
from __future__ import annotations

import re
from typing import Any

_PHONE_DIGITS_PATTERN = re.compile(r"\D")
# \b(단어 경계)를 사용하지 않는다 — 한국어 텍스트(예: "학부모010-1234-5678")에서
# 깨진 인코딩이나 한글 byte 옆에서는 \b가 매칭되지 않을 수 있다.
# 대신 양옆에 숫자가 더 붙지 않는지만 lookahead/lookbehind로 확인.
_PHONE_INLINE_PATTERN = re.compile(
    r"(?<!\d)01[016789][-\s.]?\d{3,4}[-\s.]?\d{4}(?!\d)"
)


def mask_phone(value: Any) -> str:
    """전화번호 마스킹: 010-1234-5678 → 010-****-5678. 형태 보존."""
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    digits = _PHONE_DIGITS_PATTERN.sub("", s)
    if len(digits) >= 8 and digits.startswith("01"):
        # 휴대폰: 앞 3 + **** + 뒤 4
        return f"{digits[:3]}-****-{digits[-4:]}"
    if len(digits) >= 8:
        return f"{digits[:3]}-****-{digits[-4:]}"
    # 너무 짧으면 모두 마스킹
    return "*" * len(s)


def mask_name(value: Any) -> str:
    """이름 마스킹: 홍길동 → 홍**, 김민서 → 김**, 박이 → 박*."""
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    if len(s) <= 1:
        return "*"
    return s[0] + "*" * (len(s) - 1)


def mask_inline_phones(text: Any) -> str:
    """텍스트 안 010-XXXX-XXXX 패턴을 010-****-XXXX로 치환."""
    if text is None:
        return ""
    s = str(text)
    return _PHONE_INLINE_PATTERN.sub(
        lambda m: mask_phone(m.group(0)), s
    )


def mask_sample_for_llm(value: Any) -> str:
    """엑셀 샘플 등 외부 LLM 전송용 범용 마스킹.
    숫자 8자리 이상 = 전화번호로 추정해 마스킹, 나머지는 inline 전화번호 패턴만 마스킹."""
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    digits = _PHONE_DIGITS_PATTERN.sub("", s)
    if len(digits) >= 8:
        return mask_phone(s)
    return mask_inline_phones(s)
