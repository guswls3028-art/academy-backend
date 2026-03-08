# apps/support/messaging/policy.py
"""
메시징 발송 정책 및 채널 resolver — 단일 진입점.

- SMS: OWNER_TENANT_ID(내 테넌트)에서만 허용.
- 알림톡: 모든 tenant 허용. tenant별 kakao_pfid 있으면 해당 채널, 없으면 시스템 기본 채널.
"""

import logging
from typing import Optional

from django.conf import settings

logger = logging.getLogger(__name__)


def get_owner_tenant_id() -> int:
    """SMS 발송이 허용된 tenant ID (내 테넌트)."""
    return getattr(settings, "OWNER_TENANT_ID", 1)


def can_send_sms(tenant_id: int) -> bool:
    """해당 tenant가 문자(SMS/LMS) 발송을 허용하는지 여부."""
    return int(tenant_id) == get_owner_tenant_id()


class MessagingPolicyError(Exception):
    """메시징 정책 위반 (예: 비허용 tenant의 SMS 요청)."""
    def __init__(self, message: str, reason: str = "policy"):
        super().__init__(message)
        self.reason = reason


def resolve_kakao_channel(tenant_id: int) -> dict:
    """
    알림톡 발송 시 사용할 카카오 채널(PF ID) 결정.

    우선순위: (1) tenant 연동 채널(kakao_pfid) (2) 시스템 기본 채널(settings.SOLAPI_KAKAO_PF_ID).

    Returns:
        {"pf_id": str, "use_default": bool}
        - use_default: True면 tenant 자체 채널 미사용, 시스템 기본 채널 사용 중.
    """
    pf_id_tenant = ""
    if tenant_id is not None:
        try:
            from apps.support.messaging.credit_services import get_tenant_messaging_info
            info = get_tenant_messaging_info(int(tenant_id))
            if info:
                pf_id_tenant = (info.get("kakao_pfid") or "").strip()
        except Exception as e:
            logger.warning("resolve_kakao_channel get_tenant_messaging_info failed: %s", e)
    default_pf_id = (getattr(settings, "SOLAPI_KAKAO_PF_ID", None) or "").strip()
    if pf_id_tenant:
        return {"pf_id": pf_id_tenant, "use_default": False}
    return {"pf_id": default_pf_id or "", "use_default": True}


def resolve_messaging_provider(tenant_id: int, message_type: str) -> dict:
    """
    발송 유형별 허용 여부 및 채널 정보를 한 곳에서 결정.

    Args:
        tenant_id: 테넌트 ID
        message_type: "sms" | "alimtalk"

    Returns:
        - message_type == "sms":
          {"allowed": bool, "reason": str | None}
        - message_type == "alimtalk":
          {"allowed": True, "pf_id": str, "use_default": bool}
    """
    tenant_id = int(tenant_id)
    if message_type == "sms":
        allowed = can_send_sms(tenant_id)
        return {
            "allowed": allowed,
            "reason": None if allowed else "sms_allowed_only_for_owner_tenant",
        }
    if message_type == "alimtalk":
        channel = resolve_kakao_channel(tenant_id)
        return {
            "allowed": True,
            "pf_id": channel["pf_id"],
            "use_default": channel["use_default"],
        }
    return {"allowed": False, "reason": "unknown_message_type"}
