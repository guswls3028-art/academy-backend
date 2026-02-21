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


def _get_video_cancel_key(tenant_id: int, video_id: int) -> str:
    """재시도 시 기존 인코딩 취소 요청 Redis 키"""
    return f"tenant:{tenant_id}:video:{video_id}:cancel_requested"


def _get_video_heartbeat_key(tenant_id: int, video_id: int) -> str:
    """비디오 워커 하트비트 Redis 키 (Telemetry only, ownership=DB)"""
    return f"tenant:{tenant_id}:video:{video_id}:heartbeat"


VIDEO_ASG_INTERRUPT_KEY = "video:asg:interrupt"
VIDEO_ASG_INTERRUPT_TTL_SECONDS = 180


def set_asg_interrupt(ttl_seconds: int = VIDEO_ASG_INTERRUPT_TTL_SECONDS) -> bool:
    """Spot/Scale-in drain 시 설정. Lambda가 BacklogCount 퍼블리시 스킵하여 scale-out runaway 방지."""
    try:
        redis_client = get_redis_client()
        if not redis_client:
            return False
        redis_client.setex(VIDEO_ASG_INTERRUPT_KEY, ttl_seconds, "1")
        return True
    except Exception as e:
        logger.warning("Failed to set asg interrupt in Redis: %s", e)
        return False


def is_asg_interrupt() -> bool:
    """Lambda: interrupt 플래그 존재 시 True (metric publish skip)."""
    try:
        redis_client = get_redis_client()
        if not redis_client:
            return False
        return bool(redis_client.get(VIDEO_ASG_INTERRUPT_KEY))
    except Exception as e:
        logger.debug("Failed to check asg interrupt in Redis: %s", e)
        return False


VIDEO_HEARTBEAT_TTL_SECONDS = 300


def set_video_heartbeat(tenant_id: int, video_id: int, ttl_seconds: int = VIDEO_HEARTBEAT_TTL_SECONDS) -> bool:
    """워커가 인코딩 중 주기적으로 호출. TTL 만료 시 reclaim 조건 충족."""
    try:
        redis_client = get_redis_client()
        if not redis_client:
            return False
        key = _get_video_heartbeat_key(tenant_id, video_id)
        redis_client.setex(key, ttl_seconds, "1")
        return True
    except Exception as e:
        logger.debug("Failed to set video heartbeat in Redis: %s", e)
        return False


def has_video_heartbeat(tenant_id: int, video_id: int) -> bool:
    """하트비트 존재 여부 (Reconciler에서 reclaim 조건 판단용)"""
    try:
        redis_client = get_redis_client()
        if not redis_client:
            return False
        key = _get_video_heartbeat_key(tenant_id, video_id)
        return bool(redis_client.exists(key))
    except Exception:
        return False


def delete_video_heartbeat(tenant_id: int, video_id: int) -> bool:
    """완료/실패 시 하트비트 삭제 (선택, TTL에 맡겨도 됨)"""
    try:
        redis_client = get_redis_client()
        if not redis_client:
            return False
        key = _get_video_heartbeat_key(tenant_id, video_id)
        redis_client.delete(key)
        return True
    except Exception:
        return False


def set_cancel_requested(tenant_id: int, video_id: int, ttl_seconds: int = 300) -> bool:
    """재시도 클릭 시 기존 진행 중 작업에 취소 요청 표시 (워커가 확인 후 스킵)."""
    try:
        redis_client = get_redis_client()
        if not redis_client:
            return False
        key = _get_video_cancel_key(tenant_id, video_id)
        redis_client.setex(key, ttl_seconds, "1")
        return True
    except Exception as e:
        logger.warning("Failed to set cancel_requested in Redis: %s", e)
        return False


def is_cancel_requested(tenant_id: int, video_id: int) -> bool:
    """해당 비디오에 대해 취소 요청이 있는지 확인."""
    try:
        redis_client = get_redis_client()
        if not redis_client:
            return False
        key = _get_video_cancel_key(tenant_id, video_id)
        return bool(redis_client.get(key))
    except Exception as e:
        logger.debug("Failed to check cancel_requested in Redis: %s", e)
        return False


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


# ---- Video backlog counter (Lambda BacklogCount metric, no DB) ----
# Key: tenant:{tenant_id}:video:backlog_count. INCR on enqueue, DECR on claim/dead. Sum for endpoint.


def _video_backlog_key(tenant_id: int) -> str:
    return f"tenant:{tenant_id}:video:backlog_count"


VIDEO_BACKLOG_KEY_PATTERN = "tenant:*:video:backlog_count"


def redis_incr_video_backlog(tenant_id: int) -> bool:
    """Job enqueue 시 호출. tenant:{tenant_id}:video:backlog_count INCR."""
    try:
        redis_client = get_redis_client()
        if not redis_client:
            return False
        redis_client.incr(_video_backlog_key(tenant_id))
        return True
    except Exception as e:
        logger.warning("Redis INCR video backlog failed (tenant_id=%s): %s", tenant_id, e)
        return False


def redis_decr_video_backlog(tenant_id: int) -> bool:
    """Job claim(RUNNING) 또는 job_mark_dead(DEAD) 시 호출. 0 이하면 DECR 하지 않음."""
    try:
        redis_client = get_redis_client()
        if not redis_client:
            return False
        key = _video_backlog_key(tenant_id)
        val = redis_client.get(key)
        if val is not None and int(val) > 0:
            redis_client.decr(key)
        return True
    except Exception as e:
        logger.warning("Redis DECR video backlog failed (tenant_id=%s): %s", tenant_id, e)
        return False


def redis_get_video_backlog_total() -> int:
    """
    모든 tenant backlog_count 합계. RDS 접근 없음, O(keys) Redis만.
    Lambda /internal/video/backlog/ 응답용.
    """
    try:
        redis_client = get_redis_client()
        if not redis_client:
            return 0
        total = 0
        for key in redis_client.scan_iter(match=VIDEO_BACKLOG_KEY_PATTERN, count=100):
            try:
                val = redis_client.get(key)
                if val is not None:
                    total += int(val)
            except (ValueError, TypeError):
                continue
        return max(0, total)
    except Exception as e:
        logger.warning("Redis get video backlog total failed: %s", e)
        return 0
