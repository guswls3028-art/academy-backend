
from __future__ import annotations

from pathlib import Path


def effective_min_segments(
    configured_min_segments: int | str | None,
    *,
    duration_seconds: float | int | None = None,
    hls_time_seconds: float | int | None = None,
) -> int:
    """Return the per-variant segment floor for this media.

    Very short clips can legitimately produce fewer HLS segments than the
    configured floor. For those clips, a single playable segment is enough.
    """
    try:
        configured_min = max(1, int(configured_min_segments or 1))
    except (TypeError, ValueError):
        configured_min = 1

    if configured_min <= 1:
        return 1

    try:
        duration = float(duration_seconds) if duration_seconds is not None else None
    except (TypeError, ValueError):
        duration = None

    try:
        hls_time = float(hls_time_seconds) if hls_time_seconds is not None else None
    except (TypeError, ValueError):
        hls_time = None

    if duration and duration > 0 and hls_time and hls_time > 0:
        if duration < configured_min * hls_time:
            return 1

    return configured_min


def validate_hls_output(
    root: Path,
    min_segments: int,
    *,
    duration_seconds: float | int | None = None,
    hls_time_seconds: float | int | None = None,
) -> None:
    """
    인코딩 결과 깨짐 자동 fail 처리용 검증:
    - master.m3u8 존재
    - 각 variant playlist 존재
    - 각 variant에 최소 세그먼트 수 확보

    상품 레벨 보정:
    - 짧은 영상(총 길이 < min_segments * HLS_TIME)의 경우
      ffmpeg 정상 동작에서도 세그먼트 수가 min보다 작을 수 있음
    - 따라서 "0개"만 실패로 간주하고, 1개 이상이면 정상 처리
    """
    required_segments = effective_min_segments(
        min_segments,
        duration_seconds=duration_seconds,
        hls_time_seconds=hls_time_seconds,
    )
    master = root / "master.m3u8"
    if not master.exists():
        raise RuntimeError("master.m3u8 missing")

    variants = list(root.glob("v*/index.m3u8"))
    if not variants:
        raise RuntimeError("no variant playlists (v*/index.m3u8)")

    for v in variants:
        segs = list(v.parent.glob("*.ts"))
        if len(segs) < required_segments:
            raise RuntimeError(
                f"HLS validation failed: {v} segments={len(segs)} min={required_segments}"
            )
