# apps/support/messaging/solapi_template_client.py
"""
솔라피 알림톡 템플릿 등록(검수 신청) — REST API 연동

- POST /kakao/v2/templates (channelId=PFID, name, content, categoryCode)
- #{변수명} 형식 검증
- API Key 인증: HMAC-SHA256 (date + salt → signature)
"""

import hmac
import hashlib
import logging
import re
import secrets
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

SOLAPI_BASE = "https://api.solapi.com"
TEMPLATE_CREATE_PATH = "/kakao/v2/templates"

# #{변수명} 형식 검증 시 참고 (필요 시 확장)
VARIABLE_PATTERN = re.compile(r"#\{[^}]+\}")


def validate_template_variables(*texts: str) -> tuple[bool, list[str]]:
    """
    템플릿 본문/제목에 #{변수명} 형식이 유지되는지 검증.
    - #{변수명} 형태가 올바르게 닫혀 있는지 확인 ( #{ ... } )
    - 닫히지 않은 #{ 가 있으면 에러
    반환: (성공 여부, 에러 메시지 리스트)
    """
    errors = []
    for raw in texts:
        if not raw or not raw.strip():
            continue
        idx = 0
        while True:
            open_brace = raw.find("#{", idx)
            if open_brace == -1:
                break
            close_brace = raw.find("}", open_brace)
            if close_brace == -1:
                errors.append("'#{'에 대응하는 '}'가 없습니다.")
                break
            idx = close_brace + 1
    return (len(errors) == 0, errors)


def _create_auth_header(api_key: str, api_secret: str) -> str:
    """HMAC-SHA256 Authorization 헤더 생성 (Solapi 규격)."""
    date_time = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    salt = secrets.token_hex(16)
    data = date_time + salt
    signature = hmac.new(
        api_secret.encode(),
        data.encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"HMAC-SHA256 apiKey={api_key}, date={date_time}, salt={salt}, signature={signature}"


def create_kakao_template(
    api_key: str,
    api_secret: str,
    channel_id: str,
    name: str,
    content: str,
    category_code: str = "TE",
    message_type: str = "BA",
    emphasize_type: str = "NONE",
) -> dict:
    """
    솔라피 알림톡 템플릿 등록(검수 신청).

    Args:
        api_key: SOLAPI API Key
        api_secret: SOLAPI API Secret
        channel_id: 카카오 채널 ID (테넌트 kakao_pfid)
        name: 템플릿 이름
        content: 본문 (변수 #{변수명} 포함 가능)
        category_code: 카테고리 코드 (기본 TE)
        message_type: BA(기본형) 등
        emphasize_type: NONE 등

    Returns:
        {"templateId": "KA01TP..."} 또는 예외

    Raises:
        ValueError: 검증 실패 또는 API 에러
    """
    channel_id = (channel_id or "").strip()
    if not channel_id:
        raise ValueError("channelId(PFID)가 필요합니다.")
    if not (name or "").strip():
        raise ValueError("템플릿 이름이 필요합니다.")
    if not (content or "").strip():
        raise ValueError("템플릿 본문이 필요합니다.")

    ok, errs = validate_template_variables(content)
    if not ok:
        raise ValueError("변수 검증 실패: " + "; ".join(errs))

    url = SOLAPI_BASE + TEMPLATE_CREATE_PATH
    headers = {
        "Authorization": _create_auth_header(api_key, api_secret),
        "Content-Type": "application/json",
    }
    body = {
        "channelId": channel_id,
        "name": name.strip(),
        "content": content.strip(),
        "categoryCode": (category_code or "TE").strip(),
        "messageType": message_type,
        "emphasizeType": emphasize_type,
    }

    logger.info("Solapi template create request channelId=%s name=%s", channel_id, name[:30])
    resp = requests.post(url, json=body, headers=headers, timeout=30)

    if resp.status_code != 200:
        try:
            err_body = resp.json()
            msg = err_body.get("errorMessage") or err_body.get("message") or resp.text
        except Exception:
            msg = resp.text
        logger.warning("Solapi template create failed status=%s body=%s", resp.status_code, msg)
        raise ValueError(f"솔라피 템플릿 등록 실패: {msg}")

    data = resp.json()
    template_id = (data.get("templateId") or data.get("id") or "").strip()
    if not template_id:
        raise ValueError("솔라피 응답에 templateId가 없습니다.")
    return {"templateId": template_id, "raw": data}
