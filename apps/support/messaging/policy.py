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


def get_test_tenant_id() -> int:
    """로컬 기능 테스트용 tenant ID. 이 tenant에서는 알림톡·문자 발송 없이 기능만 동작."""
    return getattr(settings, "TEST_TENANT_ID", 9999)


def is_messaging_disabled(tenant_id: int) -> bool:
    """해당 tenant가 메시징(알림톡·문자) 비활성화(테스트용)인지. True면 발송하지 않고 스킵."""
    return int(tenant_id) == get_test_tenant_id()


def can_send_sms(tenant_id: int) -> bool:
    """해당 tenant가 문자(SMS/LMS) 발송을 허용하는지 여부."""
    if is_messaging_disabled(tenant_id):
        return False
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
    테스트 tenant(9999)에서는 발송 스킵을 위해 pf_id 빈 문자열 반환.
    """
    if is_messaging_disabled(tenant_id):
        return {"pf_id": "", "use_default": True}
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


def get_tenant_provider(tenant_id: int) -> str:
    """
    테넌트의 메시징 공급자(solapi/ppurio) 반환.
    DB 조회 실패 시 기본값 'solapi'.
    """
    try:
        from apps.core.models import Tenant
        provider = (
            Tenant.objects.filter(pk=int(tenant_id))
            .values_list("messaging_provider", flat=True)
            .first()
        )
        return (provider or "solapi").strip().lower()
    except Exception as e:
        logger.warning("get_tenant_provider failed: %s", e)
        return "solapi"


def get_tenant_own_credentials(tenant_id: int) -> dict:
    """
    테넌트 자체 연동 키 반환. 직접 연동 모드에서 사용.
    Returns: {"solapi_api_key", "solapi_api_secret", "ppurio_api_key", "ppurio_account", "provider"}
    비어 있으면 시스템 기본 키 사용.
    """
    try:
        from apps.core.models import Tenant
        t = Tenant.objects.filter(pk=int(tenant_id)).values(
            "messaging_provider",
            "own_solapi_api_key", "own_solapi_api_secret",
            "own_ppurio_api_key", "own_ppurio_account",
        ).first()
        if not t:
            return {}
        provider = (t.get("messaging_provider") or "solapi").strip().lower()
        return {
            "provider": provider,
            "solapi_api_key": (t.get("own_solapi_api_key") or "").strip(),
            "solapi_api_secret": (t.get("own_solapi_api_secret") or "").strip(),
            "ppurio_api_key": (t.get("own_ppurio_api_key") or "").strip(),
            "ppurio_account": (t.get("own_ppurio_account") or "").strip(),
        }
    except Exception as e:
        logger.warning("get_tenant_own_credentials failed: %s", e)
        return {}


def resolve_messaging_provider(tenant_id: int, message_type: str) -> dict:
    """
    발송 유형별 허용 여부 및 채널 정보를 한 곳에서 결정.

    Args:
        tenant_id: 테넌트 ID
        message_type: "sms" | "alimtalk"

    Returns:
        - message_type == "sms":
          {"allowed": bool, "reason": str | None, "provider": str}
        - message_type == "alimtalk":
          {"allowed": True, "pf_id": str, "use_default": bool, "provider": str}
    """
    tenant_id = int(tenant_id)
    provider = get_tenant_provider(tenant_id)
    if message_type == "sms":
        allowed = can_send_sms(tenant_id)
        return {
            "allowed": allowed,
            "reason": None if allowed else "sms_allowed_only_for_owner_tenant",
            "provider": provider,
        }
    if message_type == "alimtalk":
        channel = resolve_kakao_channel(tenant_id)
        return {
            "allowed": True,
            "pf_id": channel["pf_id"],
            "use_default": channel["use_default"],
            "provider": provider,
        }
    return {"allowed": False, "reason": "unknown_message_type", "provider": provider}
