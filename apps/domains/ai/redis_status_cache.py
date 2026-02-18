"""AI Job 상태 Redis 캐싱 헬퍼 (Tenant 네임스페이스)"""
from typing import Optional, Dict, Any
from libs.redis.client import get_redis_client
import json
import logging

logger = logging.getLogger(__name__)


def _get_job_status_key(tenant_id: str, job_id: str) -> str:
    """Job 상태 Redis 키 (Tenant 네임스페이스)"""
    return f"tenant:{tenant_id}:job:{job_id}:status"


def _get_job_progress_key(tenant_id: str, job_id: str) -> str:
    """Job 진행률 Redis 키 (Tenant 네임스페이스)"""
    return f"tenant:{tenant_id}:job:{job_id}:progress"


def get_job_status_from_redis(tenant_id: str, job_id: str) -> Optional[Dict[str, Any]]:
    """Redis에서 Job 상태 조회 (Tenant 검증)"""
    try:
        redis_client = get_redis_client()
        if not redis_client:
            return None
        
        key = _get_job_status_key(tenant_id, job_id)
        cached_data = redis_client.get(key)
        if not cached_data:
            return None
        
        return json.loads(cached_data)
    except Exception as e:
        logger.debug("Redis job status lookup failed: %s", e)
        return None


def cache_job_status(
    tenant_id: str,
    job_id: str,
    status: str,
    job_type: Optional[str] = None,
    error_message: Optional[str] = None,
    result: Optional[Dict[str, Any]] = None,
    ttl: Optional[int] = None,  # None이면 TTL 없음
) -> bool:
    """Job 상태를 Redis에 캐싱 (Tenant 네임스페이스, 완료 시 result 포함)"""
    try:
        redis_client = get_redis_client()
        if not redis_client:
            return False
        
        status_data = {
            "status": status,
        }
        if job_type is not None:
            status_data["job_type"] = job_type
        if error_message is not None:
            status_data["error_message"] = error_message
        if result is not None:
            # result 크기 체크 (10KB 이하만 Redis 저장)
            import json as json_module
            result_size = len(json_module.dumps(result))
            if result_size < 10000:  # 10KB 이하면 Redis에 저장
                status_data["result"] = result
            else:
                logger.info("Result payload too large (%d bytes), skipping Redis cache", result_size)
        
        key = _get_job_status_key(tenant_id, job_id)
        if ttl is None:
            # TTL 없음 (완료 상태)
            redis_client.set(key, json.dumps(status_data, default=str))
        else:
            # TTL 설정 (진행 중 상태)
            redis_client.setex(key, ttl, json.dumps(status_data, default=str))
        
        return True
    except Exception as e:
        logger.warning("Failed to cache job status in Redis: %s", e)
        return False


def refresh_job_progress_ttl(tenant_id: str, job_id: str, ttl: int = 21600) -> bool:
    """Job 진행률 TTL 슬라이딩 갱신 (6시간)"""
    try:
        redis_client = get_redis_client()
        if not redis_client:
            return False
        
        key = _get_job_progress_key(tenant_id, job_id)
        if redis_client.exists(key):
            redis_client.expire(key, ttl)
            return True
        return False
    except Exception as e:
        logger.debug("Failed to refresh job progress TTL: %s", e)
        return False
