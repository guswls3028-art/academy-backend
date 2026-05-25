"""
Ppurio message client.

This support module owns the HTTP dependency so the messaging domain can expose
thin compatibility wrappers without importing infrastructure directly.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

import requests
from django.conf import settings
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)

DEFAULT_API_URL = "https://message.ppurio.com"


def _get_ppurio_credentials(
    *, api_key: str = "", account: str = "",
) -> dict:
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


def _token_failure_reason(status_code: int) -> str:
    if status_code in (401, 403):
        return "ppurio_token_rejected"
    if 400 <= status_code < 500:
        return f"ppurio_token_client_error_{status_code}"
    return "ppurio_token_unavailable"


def _get_access_token_result(creds: dict) -> tuple[Optional[str], str]:
    account = creds["account"]
    api_key = creds["api_key"]
    if not account or not api_key:
        return None, "ppurio_not_configured"

    url = f"{creds['api_url']}/v1/token"

    try:
        resp = requests.post(
            url,
            auth=HTTPBasicAuth(account, api_key),
            timeout=10,
        )
    except requests.RequestException as e:
        logger.warning("ppurio token request unavailable: %s", e)
        return None, "ppurio_token_unavailable"
    except Exception as e:
        logger.warning("ppurio token request failed: %s", e)
        return None, "ppurio_token_unavailable"

    if resp.status_code != 200:
        try:
            data = resp.json() if resp.text else {}
        except ValueError:
            data = {}
        logger.warning(
            "ppurio token failed: status=%s code=%s desc=%s",
            resp.status_code,
            data.get("code"),
            data.get("description"),
        )
        return None, _token_failure_reason(resp.status_code)

    try:
        data = resp.json()
    except ValueError:
        logger.warning("ppurio token response invalid JSON")
        return None, "ppurio_token_unavailable"

    token = data.get("token") or data.get("accesstoken")
    if not token:
        return None, "ppurio_token_unavailable"
    return token, ""


def _get_access_token(creds: dict) -> Optional[str]:
    token, _reason = _get_access_token_result(creds)
    return token


def _generate_refkey() -> str:
    return uuid.uuid4().hex[:32]


def send_ppurio_sms(
    to: str,
    text: str,
    sender: str,
    *,
    api_key: str = "",
    account: str = "",
) -> dict:
    creds = _get_ppurio_credentials(api_key=api_key, account=account)
    if not creds["account"] or not creds["api_key"]:
        return {"status": "skipped", "reason": "ppurio_not_configured"}

    token, token_reason = _get_access_token_result(creds)
    if not token:
        return {"status": "error", "reason": token_reason}

    to = (to or "").replace("-", "").strip()
    text = (text or "").strip()
    sender = (sender or "").replace("-", "").strip()

    if not to or not text or not sender:
        return {"status": "error", "reason": "to_text_sender_required"}

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
    creds = _get_ppurio_credentials(api_key=api_key, account=account)
    if not creds["account"] or not creds["api_key"]:
        return {"status": "error", "reason": "ppurio_not_configured"}

    token, token_reason = _get_access_token_result(creds)
    if not token:
        return {"status": "error", "reason": token_reason}

    to = (to or "").replace("-", "").strip()
    sender = (sender or "").replace("-", "").strip()

    if not to or not pf_id or not template_id:
        return {"status": "error", "reason": "to_pf_template_required"}

    if pf_id and not pf_id.startswith("@"):
        logger.warning(
            "ppurio alimtalk: senderProfile '%s' does not start with '@'. "
            "Ppurio requires @channelSearchId format. Solapi-format PFID is not compatible.",
            pf_id[:20],
        )
        return {"status": "error", "reason": "invalid_sender_profile_format"}

    refkey = _generate_refkey()
    target: dict = {"to": to}

    if replacements:
        change_word = {}
        for r in replacements:
            if isinstance(r, dict) and "key" in r and "value" in r:
                change_word[r["key"]] = r["value"]
        if change_word:
            target["changeWord"] = change_word

    payload = {
        "account": creds["account"],
        "messageType": "ALT",
        "senderProfile": pf_id,
        "templateCode": template_id,
        "duplicateFlag": "N",
        "isResend": "N",
        "refKey": refkey,
        "targetCount": 1,
        "targets": [target],
    }

    try:
        resp = requests.post(
            f"{creds['api_url']}/v1/kakao",
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
