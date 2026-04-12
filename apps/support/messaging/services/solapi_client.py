# apps/support/messaging/services/solapi_client.py
# SSOT 문서: backend/.claude/domains/messaging.md (수정 시 문서도 동기화)
"""
Solapi 클라이언트 — 인증 정보, Mock 모드, SMS 즉시 발송
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
        from apps.support.messaging.solapi_mock import MockSolapiMessageService
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
    SMS/LMS 즉시 발송 (Solapi).

    Args:
        to: 수신 번호 (01012345678)
        text: 본문
        sender: 발신 번호 (미지정 시 SOLAPI_SENDER 사용)
        tenant_id: 요청 tenant. 지정 시 해당 tenant가 SMS 허용(내 테넌트)인지 검사.

    Returns:
        dict: {"status": "ok"|"error"|"skipped", "group_id"?, "reason"?}
    """
    if tenant_id is not None:
        from apps.support.messaging.policy import can_send_sms, is_messaging_disabled
        if is_messaging_disabled(tenant_id):
            logger.info("send_sms skipped: tenant_id=%s is test tenant (messaging disabled)", tenant_id)
            return {"status": "skipped", "reason": "messaging_disabled_for_test_tenant"}
        if not can_send_sms(tenant_id):
            logger.warning(
                "send_sms blocked by policy: tenant_id=%s is not owner tenant (SMS allowed only for owner)",
                tenant_id,
            )
            return {"status": "error", "reason": "sms_allowed_only_for_owner_tenant"}

    client = get_solapi_client()
    if not client:
        logger.info("send_sms skipped: Solapi not configured")
        return {"status": "skipped", "reason": "solapi_not_configured"}

    sender = (sender or "").strip() or os.environ.get("SOLAPI_SENDER") or getattr(settings, "SOLAPI_SENDER", "")
    if not sender:
        return {"status": "error", "reason": "sender_required"}

    to = (to or "").replace("-", "").strip()
    if not to or not (text or "").strip():
        return {"status": "error", "reason": "to_and_text_required"}

    try:
        from solapi.model import RequestMessage
        message = RequestMessage(from_=sender, to=to, text=text.strip())
        response = client.send(message)
        group_id = getattr(getattr(response, "group_info", None), "group_id", None)
        logger.info("send_sms ok to=%s group_id=%s", to[:4] + "****", group_id)
        return {"status": "ok", "group_id": group_id}
    except Exception as e:
        logger.exception("send_sms failed to=%s", to[:4] + "****")
        return {"status": "error", "reason": str(e)[:500]}
