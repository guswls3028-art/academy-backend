# apps/support/messaging/solapi_sender_client.py
"""
솔라피 발신번호 조회 — 활성화된 발신번호 목록 확인

- GET /senderid/v1/numbers/active
- HMAC-SHA256 인증 (solapi_template_client와 동일)
"""

import logging
import re
from typing import Optional

import requests

from apps.support.messaging.solapi_template_client import _create_auth_header

logger = logging.getLogger(__name__)

SOLAPI_BASE = "https://api.solapi.com"
SENDER_ACTIVE_PATH = "/senderid/v1/numbers/active"


def _normalize_phone(phone: str) -> str:
    """휴대폰 번호 정규화: 하이픈 제거, 숫자만."""
    return re.sub(r"\D", "", phone or "")


def get_active_sender_numbers(api_key: str, api_secret: str) -> list[str]:
    """
    솔라피에 등록된 활성 발신번호 목록 조회.

    Returns:
        정규화된 전화번호 문자열 리스트 (예: ["01012345678", "01087654321"])
    """
    url = SOLAPI_BASE + SENDER_ACTIVE_PATH
    headers = {
        "Authorization": _create_auth_header(api_key, api_secret),
        "Content-Type": "application/json",
    }
    resp = requests.get(url, headers=headers, timeout=15)
    if resp.status_code != 200:
        try:
            err = resp.json()
            msg = err.get("errorMessage") or err.get("message") or resp.text
        except Exception:
            msg = resp.text
        logger.warning("Solapi sender list failed status=%s body=%s", resp.status_code, msg)
        raise ValueError(f"솔라피 발신번호 조회 실패: {msg}")

    data = resp.json()
    numbers: list[str] = []

    # 응답 형식 유연 처리: list, list[].phoneNumber, list[].number 등
    raw_list = data.get("list") or data.get("numbers") or data.get("data") or []
    if isinstance(data, list):
        raw_list = data

    for item in raw_list:
        if isinstance(item, str):
            n = _normalize_phone(item)
            if n and len(n) >= 10:
                numbers.append(n)
        elif isinstance(item, dict):
            for key in ("phoneNumber", "number", "senderId", "phone_number"):
                val = item.get(key)
                if val:
                    n = _normalize_phone(str(val))
                    if n and len(n) >= 10:
                        numbers.append(n)
                        break

    return list(dict.fromkeys(numbers))  # 중복 제거


def verify_sender_number(
    api_key: str, api_secret: str, phone: str
) -> tuple[bool, str]:
    """
    해당 번호가 솔라피에 등록·활성화된 발신번호인지 확인.

    Returns:
        (verified, message)
    """
    phone = _normalize_phone(phone or "")
    if not phone or len(phone) < 10:
        return False, "올바른 휴대폰 번호를 입력해 주세요."

    try:
        active = get_active_sender_numbers(api_key, api_secret)
    except ValueError as e:
        return False, str(e)

    if phone in active:
        return True, "솔라피에 등록된 발신번호입니다."
    return False, "솔라피에 등록되지 않은 번호입니다. 콘솔(console.solapi.com)에서 발신번호를 등록·인증해 주세요."
