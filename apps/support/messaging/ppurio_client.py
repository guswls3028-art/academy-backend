# apps/support/messaging/ppurio_client.py
"""
뿌리오(Ppurio) 메시지 발송 클라이언트

- 알림톡 / SMS / LMS 발송 지원
- 환경변수: PPURIO_API_KEY, PPURIO_ACCOUNT, PPURIO_API_URL
- 뿌리오 REST API 사용
"""

import base64
import json
import logging
import os
from typing import Optional

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# 뿌리오 API 기본 URL
DEFAULT_API_URL = "https://message.ppurio.com"


def _get_ppurio_credentials() -> dict:
    """뿌리오 인증 정보 (환경변수 우선, settings fallback)."""
    return {
        "api_key": os.environ.get("PPURIO_API_KEY") or getattr(settings, "PPURIO_API_KEY", ""),
        "account": os.environ.get("PPURIO_ACCOUNT") or getattr(settings, "PPURIO_ACCOUNT", ""),
        "api_url": (
            os.environ.get("PPURIO_API_URL")
            or getattr(settings, "PPURIO_API_URL", DEFAULT_API_URL)
        ).rstrip("/"),
    }


def _get_access_token(creds: dict) -> Optional[str]:
    """뿌리오 OAuth 토큰 발급 (Basic Auth → Bearer Token)."""
    account = creds["account"]
    api_key = creds["api_key"]
    if not account or not api_key:
        return None

    auth_str = base64.b64encode(f"{account}:{api_key}".encode()).decode()
    url = f"{creds['api_url']}/v1/token"

    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Basic {auth_str}",
                "Content-Type": "application/json",
            },
            json={"grant_type": "client_credentials"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("token") or data.get("access_token")
    except Exception as e:
        logger.warning("ppurio token request failed: %s", e)
        return None


def send_ppurio_sms(
    to: str,
    text: str,
    sender: str,
    *,
    api_key: str = "",
    account: str = "",
) -> dict:
    """
    뿌리오 SMS/LMS 발송.

    Returns: {"status": "ok"|"error"|"skipped", "msgkey"?, "reason"?}
    """
    creds = _get_ppurio_credentials()
    # 테넌트 자체 키 우선
    if api_key and account:
        creds["api_key"] = api_key
        creds["account"] = account
    if not creds["api_key"] or not creds["account"]:
        return {"status": "skipped", "reason": "ppurio_not_configured"}

    token = _get_access_token(creds)
    if not token:
        return {"status": "error", "reason": "ppurio_token_failed"}

    to = (to or "").replace("-", "").strip()
    text = (text or "").strip()
    sender = (sender or "").replace("-", "").strip()

    if not to or not text or not sender:
        return {"status": "error", "reason": "to_text_sender_required"}

    # 90바이트 이하: SMS, 초과: LMS
    text_bytes = text.encode("utf-8")
    msg_type = "SMS" if len(text_bytes) <= 90 else "LMS"

    payload = {
        "account": creds["account"],
        "messageType": msg_type,
        "from": sender,
        "content": text,
        "targets": [{"to": to}],
    }
    if msg_type == "LMS":
        payload["subject"] = (text[:20] + "…") if len(text) > 20 else text

    try:
        resp = requests.post(
            f"{creds['api_url']}/v1/message",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15,
        )
        data = resp.json()
        if resp.status_code in (200, 201) and data.get("code") == "1000":
            msgkey = data.get("msgkey") or data.get("messageKey")
            logger.info("ppurio SMS ok to=%s**** msgkey=%s", to[:4], msgkey)
            return {"status": "ok", "msgkey": msgkey}
        reason = data.get("description") or data.get("message") or str(resp.status_code)
        logger.warning("ppurio SMS failed to=%s****: %s", to[:4], reason)
        return {"status": "error", "reason": reason[:500]}
    except Exception as e:
        logger.exception("ppurio SMS exception to=%s****", to[:4])
        return {"status": "error", "reason": str(e)[:500]}


def send_ppurio_alimtalk(
    to: str,
    sender: str,
    pf_id: str,
    template_id: str,
    replacements: Optional[list] = None,
    *,
    api_key: str = "",
    account: str = "",
) -> dict:
    """
    뿌리오 알림톡 발송.

    Args:
        to: 수신 번호
        sender: 발신 번호
        pf_id: 카카오 비즈니스 채널 발신프로필키 (senderKey)
        template_id: 카카오 알림톡 템플릿 코드
        replacements: [{"key": "name", "value": "홍길동"}, ...]

    Returns: {"status": "ok"|"error", "msgkey"?, "reason"?}
    """
    creds = _get_ppurio_credentials()
    # 테넌트 자체 키 우선
    if api_key and account:
        creds["api_key"] = api_key
        creds["account"] = account
    if not creds["api_key"] or not creds["account"]:
        return {"status": "error", "reason": "ppurio_not_configured"}

    token = _get_access_token(creds)
    if not token:
        return {"status": "error", "reason": "ppurio_token_failed"}

    to = (to or "").replace("-", "").strip()
    sender = (sender or "").replace("-", "").strip()

    if not to or not pf_id or not template_id:
        return {"status": "error", "reason": "to_pf_template_required"}

    # 치환 변수 조립
    variables = {}
    if replacements and isinstance(replacements, list):
        for r in replacements:
            if isinstance(r, dict):
                k = r.get("key", "")
                v = r.get("value", "")
                if k:
                    variables[f"#{{{k}}}"] = v

    payload = {
        "account": creds["account"],
        "messageType": "AT",  # Alim Talk
        "from": sender,
        "targets": [{"to": to}],
        "templateCode": template_id,
        "senderKey": pf_id,
    }
    if variables:
        payload["content"] = json.dumps(variables, ensure_ascii=False)

    try:
        resp = requests.post(
            f"{creds['api_url']}/v1/message",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15,
        )
        data = resp.json()
        if resp.status_code in (200, 201) and data.get("code") == "1000":
            msgkey = data.get("msgkey") or data.get("messageKey")
            logger.info("ppurio alimtalk ok to=%s**** msgkey=%s", to[:4], msgkey)
            return {"status": "ok", "msgkey": msgkey}
        reason = data.get("description") or data.get("message") or str(resp.status_code)
        logger.warning("ppurio alimtalk failed to=%s****: %s", to[:4], reason)
        return {"status": "error", "reason": reason[:500]}
    except Exception as e:
        logger.exception("ppurio alimtalk exception to=%s****", to[:4])
        return {"status": "error", "reason": str(e)[:500]}
