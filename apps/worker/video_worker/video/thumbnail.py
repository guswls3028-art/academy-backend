from __future__ import annotations

import subprocess
from pathlib import Path

from apps.worker.video_worker.utils import ensure_dir, trim_tail


class ThumbnailError(RuntimeError):
    pass


def generate_thumbnail(
    *,
    input_path: str,
    output_path: Path,
    ffmpeg_bin: str,
    at_seconds: float,
    timeout: int,
) -> None:
    ensure_dir(output_path.parent)

    cmd = [
        ffmpeg_bin,
        "-y",
        "-ss", f"{at_seconds:.3f}",
        "-i", input_path,
        "-frames:v", "1",
        "-q:v", "2",
        str(output_path),
    ]

    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise ThumbnailError(f"thumbnail timeout ({timeout}s)") from e

    if p.returncode != 0:
        raise ThumbnailError(f"thumbnail ffmpeg failed: {trim_tail(p.stderr)}")
