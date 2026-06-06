# apps/support/messaging/services/solapi_client.py
# SSOT 문서: backend/docs/domain/messaging.md, backend/docs/domain/messaging-alimtalk.md
"""
Solapi 클라이언트 — 인증 정보, Mock 모드.

SMS/LMS 직접 발송은 정책상 비활성이다. 실발송은 SQS 공용 알림톡 경로만 사용한다.
"""

import logging
import os
from typing import Optional

from django.conf import settings

logger = logging.getLogger(__name__)


def _get_solapi_credentials() -> tuple[Optional[str], Optional[str]]:
    """Solapi API Key/Secret (환경변수 우선, 설정 fallback). 코드에 키 노출 금지."""
    key = os.environ.get("SOLAPI_API_KEY") or getattr(settings, "SOLAPI_API_KEY", None)
    secret = os.environ.get("SOLAPI_API_SECRET") or getattr(settings, "SOLAPI_API_SECRET", None)
    return (key or None, secret or None)


def _is_mock_mode() -> bool:
    """DEBUG=True 또는 SOLAPI_MOCK=true 이면 실제 API 호출 없이 Mock 사용."""
    if os.environ.get("SOLAPI_MOCK", "").lower() in ("true", "1", "yes"):
        return True
    if getattr(settings, "DEBUG", False):
        return True
    return os.environ.get("DEBUG", "").lower() in ("true", "1", "yes")


def get_solapi_client():
    """
    SolapiMessageService 인스턴스 반환.
    DEBUG=True 또는 SOLAPI_MOCK=true 이면 MockSolapiMessageService (로그만).
    키/시크릿이 없으면 None (스텁 모드).
    """
    if _is_mock_mode():
        from apps.domains.messaging.solapi_mock import MockSolapiMessageService
        key, secret = _get_solapi_credentials()
        return MockSolapiMessageService(api_key=key or "", api_secret=secret or "")
    key, secret = _get_solapi_credentials()
    if not key or not secret:
        return None
    try:
        from solapi import SolapiMessageService
        return SolapiMessageService(api_key=key, api_secret=secret)
    except ImportError as e:
        logger.warning("solapi SDK not installed: %s", e)
        return None


def send_sms(
    to: str,
    text: str,
    sender: Optional[str] = None,
    tenant_id: Optional[int] = None,
) -> dict:
    """
    SMS/LMS 즉시 발송은 정책상 비활성.

    Args:
        to: 수신 번호 (01012345678)
        text: 본문
        sender: legacy argument, ignored
        tenant_id: 요청 tenant, 로그용

    Returns:
        dict: {"status": "error", "reason": "sms_disabled"}
    """
    logger.error("send_sms blocked: SMS/LMS sending is disabled service-wide (tenant_id=%s)", tenant_id)
    return {"status": "error", "reason": "sms_disabled"}
