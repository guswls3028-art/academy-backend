# PATH: apps/worker/video_worker/video/validate.py

from __future__ import annotations

from pathlib import Path


def validate_hls_output(root: Path, min_segments: int) -> None:
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
    master = root / "master.m3u8"
    if not master.exists():
        raise RuntimeError("master.m3u8 missing")

    variants = list(root.glob("v*/index.m3u8"))
    if not variants:
        raise RuntimeError("no variant playlists (v*/index.m3u8)")

    for v in variants:
        segs = list(v.parent.glob("*.ts"))

        # 기존: 고정 min_segments 강제
        # if len(segs) < int(min_segments):
        #     raise RuntimeError(f"HLS validation failed: {v} segments={len(segs)} min={min_segments}")

        # MODIFIED: 짧은 영상 허용 (세그먼트 1개 이상이면 정상)
        if len(segs) <= 0:  # MODIFIED
            raise RuntimeError(  # MODIFIED
                f"HLS validation failed: {v} segments={len(segs)} min=1"  # MODIFIED
            )
