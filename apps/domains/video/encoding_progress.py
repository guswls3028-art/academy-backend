# Redis 기반 영상 인코딩 진행률 조회 (워커 job:video:{id}:progress 키와 동일 포맷)

from __future__ import annotations

from typing import Optional

from academy.adapters.cache.redis_video_status_cache import get_video_progress_payload as _get_progress_payload

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
def _extract_encoding_progress(payload: dict) -> Optional[int]:
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


def _extract_encoding_remaining_seconds(payload: dict) -> Optional[int]:
    if not payload:
        return None

    sec = payload.get("remaining_seconds")
    if sec is None:
        return None
    try:
        return max(0, int(sec))
    except (TypeError, ValueError):
        return None


def _extract_encoding_step_detail(payload: dict) -> Optional[dict]:
    if not payload:
        return None
    idx = payload.get("step_index")
    total = payload.get("step_total")
    display = payload.get("step_name_display")
    name = payload.get("step_name") or display
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


def get_video_encoding_snapshot(video_id: int, tenant_id: Optional[int] = None) -> dict:
    """
    Redis 진행률 payload를 한 번만 읽어 serializer에 필요한 파생 필드를 모두 계산한다.
    """
    payload = _get_progress_payload(video_id, tenant_id=tenant_id)
    if not payload:
        return {
            "progress": None,
            "remaining_seconds": None,
            "step_detail": None,
        }
    return {
        "progress": _extract_encoding_progress(payload),
        "remaining_seconds": _extract_encoding_remaining_seconds(payload),
        "step_detail": _extract_encoding_step_detail(payload),
    }


def get_video_encoding_progress(video_id: int, tenant_id: Optional[int] = None) -> Optional[int]:
    """
    Redis에서 영상 인코딩 진행률 조회.
    워커가 record_progress(job_id="video:{video_id}", step=..., extra=...) 로 기록한 값을 읽음.
    반환: 0..100 또는 None (Redis 미설정/미기록 시).
    """
    payload = _get_progress_payload(video_id, tenant_id=tenant_id)
    return _extract_encoding_progress(payload or {})


def get_video_encoding_remaining_seconds(video_id: int, tenant_id: Optional[int] = None) -> Optional[int]:
    """
    Redis에서 영상 인코딩 예상 남은 시간(초) 조회.
    워커가 record_progress 시 extra에 remaining_seconds 를 넣으면 반환.
    """
    payload = _get_progress_payload(video_id, tenant_id=tenant_id)
    return _extract_encoding_remaining_seconds(payload or {})


def get_video_encoding_step_detail(video_id: int, tenant_id: Optional[int] = None) -> Optional[dict]:
    """
    Redis에서 구간별 진행률 조회. (n/7) 단계 + 구간 내 0~100%.
    반환: { step_index, step_total, step_name, step_name_display, step_percent } 또는 None.
    """
    payload = _get_progress_payload(video_id, tenant_id=tenant_id)
    return _extract_encoding_step_detail(payload or {})
