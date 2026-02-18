"""비디오 상태 Redis 캐싱 헬퍼 (Tenant 네임스페이스)"""
from typing import Optional, Dict, Any
from libs.redis.client import get_redis_client
import json
import logging

logger = logging.getLogger(__name__)


def _get_video_status_key(tenant_id: int, video_id: int) -> str:
    """비디오 상태 Redis 키 (Tenant 네임스페이스)"""
    return f"tenant:{tenant_id}:video:{video_id}:status"


def _get_video_progress_key(tenant_id: int, video_id: int) -> str:
    """비디오 진행률 Redis 키 (Tenant 네임스페이스)"""
    return f"tenant:{tenant_id}:video:{video_id}:progress"


def get_video_status_from_redis(tenant_id: int, video_id: int) -> Optional[Dict[str, Any]]:
    """Redis에서 비디오 상태 조회 (Tenant 검증)"""
    try:
        redis_client = get_redis_client()
        if not redis_client:
            return None
        
        key = _get_video_status_key(tenant_id, video_id)
        cached_data = redis_client.get(key)
        if not cached_data:
            return None
        
        return json.loads(cached_data)
    except Exception as e:
        logger.debug("Redis video status lookup failed: %s", e)
        return None


def cache_video_status(
    tenant_id: int,
    video_id: int,
    status: str,
    hls_path: Optional[str] = None,
    duration: Optional[int] = None,
    error_reason: Optional[str] = None,
    ttl: Optional[int] = None,  # None이면 TTL 없음
) -> bool:
    """비디오 상태를 Redis에 캐싱 (Tenant 네임스페이스)"""
    try:
        redis_client = get_redis_client()
        if not redis_client:
            return False
        
        status_data = {
            "status": status,
        }
        if hls_path is not None:
            status_data["hls_path"] = hls_path
        if duration is not None:
            status_data["duration"] = duration
        if error_reason is not None:
            status_data["error_reason"] = error_reason
        
        key = _get_video_status_key(tenant_id, video_id)
        if ttl is None:
            # TTL 없음 (완료 상태)
            redis_client.set(key, json.dumps(status_data, default=str))
        else:
            # TTL 설정 (진행 중 상태)
            redis_client.setex(key, ttl, json.dumps(status_data, default=str))
        
        return True
    except Exception as e:
        logger.warning("Failed to cache video status in Redis: %s", e)
        return False


def refresh_video_progress_ttl(tenant_id: int, video_id: int, ttl: int = 21600) -> bool:
    """비디오 진행률 TTL 슬라이딩 갱신 (6시간)"""
    try:
        redis_client = get_redis_client()
        if not redis_client:
            return False
        
        key = _get_video_progress_key(tenant_id, video_id)
        if redis_client.exists(key):
            redis_client.expire(key, ttl)
            return True
        return False
    except Exception as e:
        logger.debug("Failed to refresh video progress TTL: %s", e)
        return False
