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


def get_video_encoding_progress(video_id: int) -> Optional[int]:
    """
    Redis에서 영상 인코딩 진행률 조회.
    워커가 record_progress(job_id="video:{video_id}", step=..., extra=...) 로 기록한 값을 읽음.
    반환: 0..100 또는 None (Redis 미설정/미기록 시).
    """
    try:
        from libs.redis.client import get_redis_client
    except ImportError:
        return None

    client = get_redis_client()
    if not client:
        return None

    job_id = f"{VIDEO_JOB_ID_PREFIX}{video_id}"
    key = f"job:{job_id}:progress"
    try:
        raw = client.get(key)
        if not raw:
            return None
        payload = json.loads(raw)
    except Exception:
        return None

    # extra.percent 가 있으면 우선 사용 (워커에서 세밀하게 넣을 수 있음)
    extra = payload.get("extra") if isinstance(payload.get("extra"), dict) else {}
    if not extra and isinstance(payload.get("extra"), (int, float)):
        pct = int(payload["extra"])
        return max(0, min(100, pct))
    percent = extra.get("percent")
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
