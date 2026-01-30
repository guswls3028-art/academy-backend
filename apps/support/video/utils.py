# PATH: apps/support/video/utils.py

def extract_duration_seconds_from_url(url: str) -> int | None:
    """
    ffprobe를 URL에 직접 적용 (Range Request 기반)

    ⚠️ API 서버 안전화:
    - ffmpeg 모듈 없으면 None 반환
    - API 크래시 절대 발생 ❌
    """
    if not url:
        return None

    try:
        import ffmpeg  # lazy import
    except Exception:
        return None

    try:
        probe = ffmpeg.probe(url)
        fmt = probe.get("format") or {}
        dur = fmt.get("duration")
        if dur is None:
            return None
        return int(float(dur))
    except Exception:
        return None


def generate_thumbnail_from_url(
    url: str,
    ss_seconds: int = 1,
) -> bytes | None:
    """
    URL 스트리밍 기반 썸네일 생성

    🚫 API 서버에서는 사용 금지
    ✔️ Worker 전용

    - 실패 시 None 반환
    """
    if not url:
        return None

    try:
        import ffmpeg  # lazy import
    except Exception:
        return None

    try:
        out, _ = (
            ffmpeg
            .input(url, ss=ss_seconds)
            .output(
                "pipe:",
                vframes=1,
                format="image2",
                vcodec="mjpeg",
            )
            .run(capture_stdout=True, capture_stderr=True)
        )
        return out
    except Exception:
        return None
