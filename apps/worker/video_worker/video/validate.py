from __future__ import annotations

from pathlib import Path


def validate_hls_output(root: Path, min_segments: int) -> None:
    """
    인코딩 결과 깨짐 자동 fail 처리용 검증:
    - master.m3u8 존재
    - 각 variant playlist 존재
    - 각 variant에 최소 세그먼트 수 확보
    """
    master = root / "master.m3u8"
    if not master.exists():
        raise RuntimeError("master.m3u8 missing")

    variants = list(root.glob("v*/index.m3u8"))
    if not variants:
        raise RuntimeError("no variant playlists (v*/index.m3u8)")

    for v in variants:
        segs = list(v.parent.glob("*.ts"))
        if len(segs) < int(min_segments):
            raise RuntimeError(f"HLS validation failed: {v} segments={len(segs)} min={min_segments}")
