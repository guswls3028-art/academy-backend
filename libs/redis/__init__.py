"""
Redis 보호 레이어

SQS + Worker + DB 아키텍처는 그대로 유지.
Redis는 "상태 관리 및 보호" 목적으로만 사용.

- 멱등성 (중복 실행 방지)
- 실시간 Job 상태 (SSOT)
- 영상 시청 Heartbeat 버퍼링
- Write-Behind 전략 지원

Redis 장애 시 DB 기반 로직으로 자동 fallback.
"""

from libs.redis.client import get_redis_client, is_redis_available

__all__ = [
    "get_redis_client",
    "is_redis_available",
]
