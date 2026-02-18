"""Video Progress Adapter - Video 전용 (AI와 분리, IProgress 인터페이스 구현)"""
from typing import Any, Optional
from libs.redis.client import get_redis_client
from src.application.ports.progress import IProgress
import json
import logging

logger = logging.getLogger(__name__)

# Video 진행 상태 키 TTL (6시간)
VIDEO_PROGRESS_TTL_SECONDS = 21600


class VideoProgressAdapter(IProgress):
    """Video 전용 Progress Adapter (IProgress 인터페이스 구현, AI와 분리)"""

    def __init__(self, video_id: int, tenant_id: int, ttl_seconds: int = VIDEO_PROGRESS_TTL_SECONDS) -> None:
        self._video_id = video_id
        self._tenant_id = tenant_id
        self._ttl = ttl_seconds

    def record_progress(
        self,
        job_id: str,  # IProgress 인터페이스 호환 (무시됨, video_id 사용)
        step: str,
        extra: Optional[dict[str, Any]] = None,
    ) -> None:
        """Video 진행 단계 기록 (Redis에만) - IProgress 인터페이스 구현"""
        client = get_redis_client()
        if not client:
            return

        # ✅ Video 전용 키 형식: tenant:{tenant_id}:video:{video_id}:progress
        key = f"tenant:{self._tenant_id}:video:{self._video_id}:progress"
        payload = {"step": step, **(extra or {})}
        try:
            client.setex(
                key,
                self._ttl,
                json.dumps(payload, default=str),
            )
            logger.debug("Video progress recorded: video_id=%s step=%s tenant_id=%s", self._video_id, step, self._tenant_id)
        except Exception as e:
            logger.warning("Redis video progress record failed: %s", e)

    def get_progress(self, job_id: str) -> Optional[dict[str, Any]]:
        """Video 진행 상태 조회 - IProgress 인터페이스 구현"""
        client = get_redis_client()
        if not client:
            return None

        # ✅ Video 전용 키 형식
        key = f"tenant:{self._tenant_id}:video:{self._video_id}:progress"
        
        # 하위 호환성: tenant namespace 키가 없으면 기존 키 형식 확인
        try:
            raw = client.get(key)
            if raw:
                return json.loads(raw)
        except Exception:
            pass
        
        # Legacy 키 확인 (마이그레이션 기간 동안)
        legacy_key = f"job:video:{self._video_id}:progress"
        try:
            raw = client.get(legacy_key)
            if raw:
                return json.loads(raw)
        except Exception:
            pass
        
        return None
    
    # 편의 메서드 (IProgress 외 추가)
    def get_progress_direct(self) -> Optional[dict[str, Any]]:
        """직접 조회 (job_id 없이)"""
        return self.get_progress("")  # job_id는 무시됨
