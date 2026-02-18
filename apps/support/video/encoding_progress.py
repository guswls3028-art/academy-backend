# PATH: apps/support/video/encoding_progress.py
# Redis 기반 영상 인코딩 진행률 조회 (워커 job:video:{id}:progress 키와 동일 포맷)

from __future__ import annotations

import json
from typing import Optional

# 워커 processor에서 사용하는 job_id = f"video:{video_id}"
VIDEO_JOB_ID_PREFIX = "video:"

# step → 대략적 % (워커 단계 순서와 맞춤)
_STEP_PERCENT = {
    "presigning": 5,
    "downloading": 15,
    "probing": 25,
    "transcoding": 50,
    "validating": 85,
    "thumbnail": 90,
    "uploading": 95,
}


def _get_progress_payload(video_id: int, tenant_id: Optional[int] = None) -> Optional[dict]:
    """Redis에서 tenant:{tenant_id}:video:{video_id}:progress payload 조회."""
    if not tenant_id:
        return None
    
    # ✅ VideoProgressAdapter 사용
    from apps.support.video.redis_progress_adapter import VideoProgressAdapter
    adapter = VideoProgressAdapter(video_id=video_id, tenant_id=tenant_id)
    return adapter.get_progress_direct()


def get_video_encoding_progress(video_id: int, tenant_id: Optional[int] = None) -> Optional[int]:
    """
    Redis에서 영상 인코딩 진행률 조회.
    워커가 record_progress(job_id="video:{video_id}", step=..., extra=...) 로 기록한 값을 읽음.
    반환: 0..100 또는 None (Redis 미설정/미기록 시).
    """
    payload = _get_progress_payload(video_id, tenant_id)
    if not payload:
        return None

    percent = payload.get("percent")
    if percent is not None:
        try:
            pct = int(percent)
            return max(0, min(100, pct))
        except (TypeError, ValueError):
            pass

    step = payload.get("step")
    if step in _STEP_PERCENT:
        return _STEP_PERCENT[step]
    return 50  # 알 수 없는 단계면 중간값


def get_video_encoding_remaining_seconds(video_id: int, tenant_id: Optional[int] = None) -> Optional[int]:
    """
    Redis에서 영상 인코딩 예상 남은 시간(초) 조회.
    워커가 record_progress 시 extra에 remaining_seconds 를 넣으면 반환.
    """
    payload = _get_progress_payload(video_id, tenant_id)
    if not payload:
        return None

    sec = payload.get("remaining_seconds")
    if sec is None:
        return None
    try:
        return max(0, int(sec))
    except (TypeError, ValueError):
        return None


def get_video_encoding_step_detail(video_id: int, tenant_id: Optional[int] = None) -> Optional[dict]:
    """
    Redis에서 구간별 진행률 조회. (n/7) 단계 + 구간 내 0~100%.
    반환: { step_index, step_total, step_name, step_name_display, step_percent } 또는 None.
    """
    payload = _get_progress_payload(video_id, tenant_id)
    if not payload:
        return None
    idx = payload.get("step_index")
    total = payload.get("step_total")
    name = payload.get("step_name")
    display = payload.get("step_name_display")
    pct = payload.get("step_percent")
    if idx is None or total is None or name is None or pct is None:
        return None
    try:
        return {
            "step_index": int(idx),
            "step_total": int(total),
            "step_name": str(name),
            "step_name_display": str(display) if display is not None else name,
            "step_percent": max(0, min(100, int(pct))),
        }
    except (TypeError, ValueError):
        return None
