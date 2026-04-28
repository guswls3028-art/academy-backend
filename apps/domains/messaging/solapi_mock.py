"""
Mock Solapi — DEBUG=True 일 때 실제 API 호출 없이 콘솔에 발송될 JSON만 로깅.

실제 API를 쓰면 잔액이 차감되고, 템플릿 미승인 시 에러가 나므로
개발/테스트 시에는 이 Mock을 사용.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional, Union

logger = logging.getLogger(__name__)


def _message_to_log_payload(message: Any) -> dict:
    """RequestMessage(또는 리스트)를 로그용 dict로 변환."""
    if hasattr(message, "model_dump"):
        return message.model_dump(exclude_none=True)
    if hasattr(message, "dict"):
        return message.dict(exclude_none=True)
    if isinstance(message, list):
        return [_message_to_log_payload(m) for m in message]
    if isinstance(message, dict):
        return message
    return {"raw": str(message)}


class MockSolapiMessageService:
    """
    Solapi 실제 호출 대신 로그만 출력하는 Mock.
    DEBUG=True(또는 SOLAPI_MOCK=true) 일 때 사용.
    """

    def __init__(self, api_key: str = "", api_secret: str = ""):
        self.api_key = api_key
        self.api_secret = api_secret

    def send(
        self,
        messages: Union[list, Any],
        request_config: Optional[Any] = None,
    ) -> Any:
        """
        실제 발송 대신 JSON을 예쁘게 로그하고, 성공 응답 형태의 Mock 객체 반환.
        """
        if not isinstance(messages, list):
            messages = [messages]
        request_config_payload = None
        if request_config:
            if hasattr(request_config, "model_dump"):
                request_config_payload = request_config.model_dump(exclude_none=True)
            elif hasattr(request_config, "dict"):
                request_config_payload = request_config.dict(exclude_none=True)
            else:
                request_config_payload = str(request_config)
        payload = {
            "messages": _message_to_log_payload(messages),
            "request_config": request_config_payload,
        }
        logger.info(
            "[MockSolapi] 발송 스킵 (실제 API 미호출)\n%s",
            json.dumps(payload, indent=2, ensure_ascii=False),
        )
        group_id = f"mock-{uuid.uuid4().hex[:12]}"
        return _MockSendResponse(group_id=group_id, count=len(messages))


class _MockSendResponse:
    """send() 반환값 호환용 Mock 객체."""

    def __init__(self, group_id: str, count: int = 1):
        self.group_info = _MockGroupInfo(group_id=group_id, count=count)


class _MockGroupInfo:
    def __init__(self, group_id: str, count: int = 1):
        self.group_id = group_id
        self.count = _MockCount(registered_success=count, registered_failed=0, total=count)


class _MockCount:
    def __init__(self, registered_success: int = 1, registered_failed: int = 0, total: int = 1):
        self.registered_success = registered_success
        self.registered_failed = registered_failed
        self.total = total
