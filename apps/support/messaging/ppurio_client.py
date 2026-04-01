# apps/support/messaging/ppurio_client.py
"""
뿌리오(Ppurio) 메시지 발송 클라이언트

- SMS / LMS / 알림톡 발송 지원
- 뿌리오 API: https://message.ppurio.com
- 인증: HTTPBasicAuth(계정ID, 연동인증키) → Bearer Token (24시간)
- 환경변수: PPURIO_ACCOUNT, PPURIO_API_KEY, PPURIO_API_URL
- 공식 샘플: ppurio.com → 연동 → 연동개발(API)
"""

import logging
import os
import uuid
from typing import Optional

import requests
from requests.auth import HTTPBasicAuth
from django.conf import settings

logger = logging.getLogger(__name__)

# 뿌리오 API 기본 URL
DEFAULT_API_URL = "https://message.ppurio.com"


def _get_ppurio_credentials(
    *, api_key: str = "", account: str = "",
) -> dict:
    """
    뿌리오 인증 정보 반환.

    우선순위: 함수 인자(테넌트 자체 키) > 환경변수 > Django settings
    - account: 뿌리오 계정 ID (로그인 아이디)
    - api_key: 연동 개발 인증키 (뿌리오 연동관리에서 발급)
    """
    resolved_account = (
        account
        or os.environ.get("PPURIO_ACCOUNT")
        or getattr(settings, "PPURIO_ACCOUNT", "")
    )
    resolved_api_key = (
        api_key
        or os.environ.get("PPURIO_API_KEY")
        or getattr(settings, "PPURIO_API_KEY", "")
    )
    resolved_api_url = (
        os.environ.get("PPURIO_API_URL")
        or getattr(settings, "PPURIO_API_URL", "")
        or DEFAULT_API_URL
    ).rstrip("/")

    return {
        "account": resolved_account.strip(),
        "api_key": resolved_api_key.strip(),
        "api_url": resolved_api_url,
    }


def _get_access_token(creds: dict) -> Optional[str]:
    """
    뿌리오 인증 토큰 발급.

    POST /v1/token
    Authorization: HTTPBasicAuth(account, api_key)
    응답: {"token": "...", "expired": "..."}
    토큰 유효시간: 24시간
    """
    account = creds["account"]
    api_key = creds["api_key"]
    if not account or not api_key:
        return None

    url = f"{creds['api_url']}/v1/token"

    try:
        resp = requests.post(
            url,
            auth=HTTPBasicAuth(account, api_key),
            timeout=10,
        )
        if resp.status_code != 200:
            data = resp.json() if resp.text else {}
            logger.warning(
                "ppurio token failed: status=%s code=%s desc=%s",
                resp.status_code,
                data.get("code"),
                data.get("description"),
            )
            return None
        data = resp.json()
        return data.get("token") or data.get("accesstoken")
    except Exception as e:
        logger.warning("ppurio token request failed: %s", e)
        return None


def _generate_refkey() -> str:
    """고객사 고유 키 생성."""
    return uuid.uuid4().hex[:32]


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

    POST /v1/message
    Authorization: Bearer {token}

    SMS: EUC-KR 90바이트 이하 → messageType: "SMS"
    LMS: EUC-KR 90바이트 초과 → messageType: "LMS"

    Returns: {"status": "ok"|"error"|"skipped", "refkey"?, "messagekey"?, "reason"?}
    """
    creds = _get_ppurio_credentials(api_key=api_key, account=account)
    if not creds["account"] or not creds["api_key"]:
        return {"status": "skipped", "reason": "ppurio_not_configured"}

    token = _get_access_token(creds)
    if not token:
        return {"status": "error", "reason": "ppurio_token_failed"}

    to = (to or "").replace("-", "").strip()
    text = (text or "").strip()
    sender = (sender or "").replace("-", "").strip()

    if not to or not text or not sender:
        return {"status": "error", "reason": "to_text_sender_required"}

    # EUC-KR 기준 90바이트: SMS, 초과: LMS
    try:
        text_bytes_len = len(text.encode("euc-kr", errors="replace"))
    except Exception:
        text_bytes_len = len(text.encode("utf-8"))

    refkey = _generate_refkey()
    msg_type = "SMS" if text_bytes_len <= 90 else "LMS"

    payload = {
        "account": creds["account"],
        "messageType": msg_type,
        "content": text,
        "from": sender,
        "duplicateFlag": "N",
        "refKey": refkey,
        "targetCount": 1,
        "targets": [
            {"to": to},
        ],
    }

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
        if resp.status_code in (200, 201) and "messageKey" in data:
            messagekey = data["messageKey"]
            logger.info(
                "ppurio SMS ok to=%s**** refkey=%s messagekey=%s type=%s",
                to[:4], refkey, messagekey, msg_type,
            )
            return {"status": "ok", "refkey": refkey, "messagekey": messagekey}
        code = data.get("code", "")
        reason = data.get("description") or data.get("message") or f"code={code}, http={resp.status_code}"
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
    뿌리오 알림톡(카카오톡) 발송.

    POST /v1/message
    Authorization: Bearer {token}

    messageType: "AT" (알림톡)

    Returns: {"status": "ok"|"error", "refkey"?, "messagekey"?, "reason"?}
    """
    creds = _get_ppurio_credentials(api_key=api_key, account=account)
    if not creds["account"] or not creds["api_key"]:
        return {"status": "error", "reason": "ppurio_not_configured"}

    token = _get_access_token(creds)
    if not token:
        return {"status": "error", "reason": "ppurio_token_failed"}

    to = (to or "").replace("-", "").strip()
    sender = (sender or "").replace("-", "").strip()

    if not to or not pf_id or not template_id:
        return {"status": "error", "reason": "to_pf_template_required"}

    refkey = _generate_refkey()

    payload = {
        "account": creds["account"],
        "messageType": "AT",
        "content": "",
        "from": sender,
        "refKey": refkey,
        "targetCount": 1,
        "targets": [
            {"to": to},
        ],
        "senderKey": pf_id,
        "templateCode": template_id,
    }

    # 치환 변수 적용
    if replacements:
        change_word = {}
        for r in replacements:
            if isinstance(r, dict) and "key" in r and "value" in r:
                change_word[r["key"]] = r["value"]
        if change_word:
            payload["targets"][0]["changeWord"] = change_word

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
        if resp.status_code in (200, 201) and "messageKey" in data:
            messagekey = data["messageKey"]
            logger.info(
                "ppurio alimtalk ok to=%s**** refkey=%s messagekey=%s",
                to[:4], refkey, messagekey,
            )
            return {"status": "ok", "refkey": refkey, "messagekey": messagekey}
        code = data.get("code", "")
        reason = data.get("description") or data.get("message") or f"code={code}, http={resp.status_code}"
        logger.warning("ppurio alimtalk failed to=%s****: %s", to[:4], reason)
        return {"status": "error", "reason": reason[:500]}
    except Exception as e:
        logger.exception("ppurio alimtalk exception to=%s****", to[:4])
        return {"status": "error", "reason": str(e)[:500]}
