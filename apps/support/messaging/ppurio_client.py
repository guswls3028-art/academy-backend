# apps/support/messaging/ppurio_client.py
"""
뿌리오(Ppurio) 메시지 발송 클라이언트

- 알림톡 / SMS / LMS 발송 지원
- 뿌리오 REST API v3 (https://api.bizppurio.com)
- 인증: Basic Auth → Bearer Token (24시간 유효)
- 환경변수: PPURIO_ACCOUNT, PPURIO_API_KEY, PPURIO_API_URL
- 공식 문서: https://biztech.gitbook.io/webapi
"""

import base64
import logging
import os
import uuid
from typing import Optional

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# 뿌리오 API 기본 URL (운영)
DEFAULT_API_URL = "https://api.bizppurio.com"
# 검수(테스트) 환경: https://dev-api.bizppurio.com


def _get_ppurio_credentials(
    *, api_key: str = "", account: str = "",
) -> dict:
    """
    뿌리오 인증 정보 반환.

    우선순위: 함수 인자(테넌트 자체 키) > 환경변수 > Django settings
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
    Authorization: Basic base64(account:api_key)
    응답: {"accesstoken": "eyJ...", "type": "Bearer", "expired": "20260401120000"}
    토큰 유효시간: 24시간
    """
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
                "Content-Type": "application/json; charset=utf-8",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        # 공식 응답 키: "accesstoken"
        return data.get("accesstoken") or data.get("token") or data.get("access_token")
    except Exception as e:
        logger.warning("ppurio token request failed: %s", e)
        return None


def _generate_refkey() -> str:
    """고객사 고유 키 생성 (최대 32자)."""
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

    POST /v3/message
    Authorization: Bearer {accesstoken}

    SMS: EUC-KR 90바이트 이하 → type: "sms", content.sms.message
    LMS: EUC-KR 90바이트 초과 → type: "lms", content.lms.subject + content.lms.message

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

    if text_bytes_len <= 90:
        # SMS
        payload = {
            "account": creds["account"],
            "refkey": refkey,
            "type": "sms",
            "from": sender,
            "to": to,
            "content": {
                "sms": {
                    "message": text,
                },
            },
        }
    else:
        # LMS
        subject = (text[:30] + "…") if len(text) > 30 else text
        payload = {
            "account": creds["account"],
            "refkey": refkey,
            "type": "lms",
            "from": sender,
            "to": to,
            "content": {
                "lms": {
                    "subject": subject,
                    "message": text,
                },
            },
        }

    try:
        resp = requests.post(
            f"{creds['api_url']}/v3/message",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json=payload,
            timeout=15,
        )
        data = resp.json()
        code = data.get("code")
        # 성공 코드: "1000"
        if resp.status_code in (200, 201) and code == "1000":
            messagekey = data.get("messagekey") or data.get("msgkey")
            logger.info(
                "ppurio SMS ok to=%s**** refkey=%s messagekey=%s",
                to[:4], refkey, messagekey,
            )
            return {"status": "ok", "refkey": refkey, "messagekey": messagekey}
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
    뿌리오 알림톡(AT) 발송.

    POST /v3/message
    Authorization: Bearer {accesstoken}

    type: "at"
    content.at.senderkey: 카카오 발신프로필키
    content.at.templatecode: 카카오 알림톡 템플릿 코드
    content.at.message: 치환 완료된 최종 본문 (필수)

    Args:
        to: 수신 번호 (01012345678)
        sender: 발신 번호
        pf_id: 카카오 비즈니스 채널 발신프로필키 (senderKey)
        template_id: 카카오 알림톡 템플릿 코드
        replacements: [{"key": "학생이름", "value": "길동"}, ...]

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

    # 뿌리오 알림톡: templatecode 기반 발송.
    # 카카오 알림톡은 templatecode만 있으면 카카오 서버에서 본문을 조립하므로
    # message 필드는 비워도 됨. 호출측에서 완성된 본문(text)을 전달하면 그대로 사용.
    message = ""

    payload = {
        "account": creds["account"],
        "refkey": refkey,
        "type": "at",
        "from": sender,
        "to": to,
        "content": {
            "at": {
                "senderkey": pf_id,
                "templatecode": template_id,
                "message": message,
            },
        },
    }

    try:
        resp = requests.post(
            f"{creds['api_url']}/v3/message",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json=payload,
            timeout=15,
        )
        data = resp.json()
        code = data.get("code")
        if resp.status_code in (200, 201) and code == "1000":
            messagekey = data.get("messagekey") or data.get("msgkey")
            logger.info(
                "ppurio alimtalk ok to=%s**** refkey=%s messagekey=%s",
                to[:4], refkey, messagekey,
            )
            return {"status": "ok", "refkey": refkey, "messagekey": messagekey}
        reason = data.get("description") or data.get("message") or f"code={code}, http={resp.status_code}"
        logger.warning("ppurio alimtalk failed to=%s****: %s", to[:4], reason)
        return {"status": "error", "reason": reason[:500]}
    except Exception as e:
        logger.exception("ppurio alimtalk exception to=%s****", to[:4])
        return {"status": "error", "reason": str(e)[:500]}
