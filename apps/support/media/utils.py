import ffmpeg


def extract_duration_seconds_from_url(url: str) -> int | None:
    """
    ffprobe를 URL에 직접 적용 (Range Request 기반)
    """
    try:
        probe = ffmpeg.probe(url)
        fmt = probe.get("format") or {}
        dur = fmt.get("duration")
        if dur is None:
            return None
        return int(float(dur))
    except Exception as e:
        print("duration 추출 실패:", e)
        return None


def generate_thumbnail_from_url(
    url: str,
    ss_seconds: int = 1,
) -> bytes | None:
    """
    URL 스트리밍 기반 썸네일 생성 (전체 다운로드 ❌)
    """
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
    except Exception as e:
        print("썸네일 생성 실패:", e)
        return None
