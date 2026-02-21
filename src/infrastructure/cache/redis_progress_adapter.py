"""
RedisProgressAdapter - IProgress Port 구현체

Write-Behind: 진행률은 Redis에만 먼저 기록. DB 부하 감소.
매 작업마다 DB를 치지 않고, 최종 완료 시에만 Repository가 DB에 기록.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from src.application.ports.progress import IProgress
from libs.redis.client import get_redis_client

logger = logging.getLogger(__name__)

# 진행 상태 키 TTL (1시간)
PROGRESS_TTL_SECONDS = 3600


class RedisProgressAdapter(IProgress):
    """IProgress 구현 (Redis, Write-Behind)"""

    def __init__(self, ttl_seconds: int = PROGRESS_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds

    def record_progress(
        self,
        job_id: str,
        step: str,
        extra: Optional[dict[str, Any]] = None,
        tenant_id: Optional[str] = None,  # ✅ 추가 (AI Job 및 Video Worker 전용)
    ) -> None:
        """진행 단계 기록 (Redis에만) - AI Job 및 Video Worker 지원"""
        client = get_redis_client()
        if not client:
            return

        # ✅ 안전 장치: tenant_id 누락 시 경고 로그
        if tenant_id is None:
            logger.warning("tenant_id missing for job_id=%s, using legacy key format", job_id)

        # ✅ 키 형식 결정:
        # - Video Worker: job_id="video:{video_id}" → tenant:{tenant_id}:video:{video_id}:progress
        # - AI Job: job_id="job_uuid" → tenant:{tenant_id}:job:{job_id}:progress
        if tenant_id:
            if job_id.startswith("video:"):
                # Video Worker 케이스: job_id에서 video_id 추출
                video_id = job_id.replace("video:", "")
                key = f"tenant:{tenant_id}:video:{video_id}:progress"
            else:
                # AI Job 케이스
                key = f"tenant:{tenant_id}:job:{job_id}:progress"
        else:
            # 하위 호환성: tenant_id 없으면 기존 키 형식 사용
            key = f"job:{job_id}:progress"
        
        payload = {"step": step, **(extra or {})}
        try:
            client.setex(
                key,
                self._ttl,
                json.dumps(payload, default=str),
            )
            logger.debug("Progress recorded: job_id=%s step=%s tenant_id=%s", job_id, step, tenant_id)
            if tenant_id and job_id.startswith("video:"):
                try:
                    vid = job_id.replace("video:", "").strip()
                    if vid.isdigit():
                        from apps.support.video.redis_status_cache import set_video_heartbeat
                        set_video_heartbeat(int(tenant_id), int(vid))
                except Exception as hb_e:
                    logger.debug("Video heartbeat set failed: %s", hb_e)
        except Exception as e:
            logger.warning("Redis progress record failed: %s", e)

    def get_progress(self, job_id: str, tenant_id: Optional[str] = None) -> Optional[dict[str, Any]]:
        """진행 상태 조회 - AI Job 및 Video Worker 지원"""
        client = get_redis_client()
        if not client:
            return None

        # ✅ 키 형식 결정:
        # - Video Worker: job_id="video:{video_id}" → tenant:{tenant_id}:video:{video_id}:progress
        # - AI Job: job_id="job_uuid" → tenant:{tenant_id}:job:{job_id}:progress
        if tenant_id:
            if job_id.startswith("video:"):
                # Video Worker 케이스: job_id에서 video_id 추출
                video_id = job_id.replace("video:", "")
                key = f"tenant:{tenant_id}:video:{video_id}:progress"
            else:
                # AI Job 케이스
                key = f"tenant:{tenant_id}:job:{job_id}:progress"
            
            try:
                raw = client.get(key)
                if raw:
                    return json.loads(raw)
            except Exception:
                pass
            
            # 하위 호환성: tenant namespace 키가 없으면 기존 키 형식 확인
            legacy_key = f"job:{job_id}:progress"
            try:
                raw = client.get(legacy_key)
                if raw:
                    return json.loads(raw)
            except Exception:
                pass
            
            return None
        else:
            # tenant_id 없으면 기존 키 형식 사용
            key = f"job:{job_id}:progress"
            try:
                raw = client.get(key)
                if not raw:
                    return None
                return json.loads(raw)
            except Exception as e:
                logger.warning("Redis progress get failed: %s", e)
                return None
