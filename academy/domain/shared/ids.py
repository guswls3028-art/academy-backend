"""
도메인 공통: ID 생성 (외부 라이브러리 없음)
"""
from __future__ import annotations

import uuid


def generate_request_id() -> str:
    """로그/추적용 짧은 요청 ID."""
    return str(uuid.uuid4())[:8]
