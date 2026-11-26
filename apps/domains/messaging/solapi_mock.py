"""
Mock Solapi — 개발 환경에서 모의 접수 ID와 건수를 기록한다.

실제 API를 쓰면 잔액이 차감되고, 템플릿 미승인 시 에러가 나므로
개발/테스트 시에는 이 Mock을 사용.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional, Union

logger = logging.getLogger(__name__)


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
        """Return a simulated receipt without serializing recipient or credential data."""
        count = len(messages) if isinstance(messages, list) else 1
        group_id = f"mock-{uuid.uuid4().hex[:12]}"
        logger.info("[MockSolapi] 모의 접수 group_id=%s count=%d", group_id, count)
        return _MockSendResponse(group_id=group_id, count=count)


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
