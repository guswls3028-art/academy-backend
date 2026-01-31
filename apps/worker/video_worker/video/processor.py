# PATH: apps/worker/video_worker/video/processor.py

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from apps.worker.video_worker.config import Config
from apps.worker.video_worker.download import download_to_file
from apps.worker.video_worker.heartbeat import HeartbeatThread
from apps.worker.video_worker.http_client import VideoAPIClient
from apps.worker.video_worker.locking import (
    LockBusyError,
    acquire_video_lock,
    release_video_lock,
)
from apps.worker.video_worker.utils import temp_workdir, trim_tail

from apps.worker.video_worker.video.duration import probe_duration_seconds
from apps.worker.video_worker.video.thumbnail import generate_thumbnail
from apps.worker.video_worker.video.transcoder import transcode_to_hls
from apps.worker.video_worker.video.validate import validate_hls_output
from apps.worker.video_worker.video.r2_uploader import upload_directory

logger = logging.getLogger("video_worker.processor")


def _safe_int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except Exception:
        return None


def _build_source_url_from_file_key(*, file_key: str) -> str:
    """
    SSOT:
    - job payload에는 file_key만 온다. (backend /internal/video-worker/next/)
    - worker는 source 다운로드용 URL을 만든다.
    - libs.s3_client.presign이 있으면 presigned GET 생성 (원본 구조 존중)
    """
    if not file_key:
        raise RuntimeError("file_key_missing")

    try:
        from libs.s3_client.presign import create_presigned_get_url  # lazy import
    except Exception as e:
        raise RuntimeError(f"presign_module_missing:{trim_tail(str(e))}") from e

    try:
        return create_presigned_get_url(key=str(file_key), expires_in=600)
    except Exception as e:
        raise RuntimeError(f"presigned_get_failed:{trim_tail(str(e))}") from e


def _hls_r2_prefix(cfg: Config, video_id: int) -> str:
    base = (cfg.R2_PREFIX or "media/hls/videos").strip("/")
    return f"{base}/{int(video_id)}"


def _hls_master_relpath(cfg: Config, video_id: int) -> str:
    return f"{_hls_r2_prefix(cfg, video_id)}/master.m3u8"


def process_video_job(*, job: Dict[str, Any], cfg: Config, client: VideoAPIClient) -> None:
    """
    main.py 계약:
    - process_video_job(job=job, cfg=cfg, client=client)
    - job은 dict이며, 최소 {"video_id": int, "file_key": str} 기대
    """
    video_id = _safe_int(job.get("video_id"))
    file_key = (job.get("file_key") or "").strip()

    if not video_id:
        raise KeyError("video_id")

    # single-host idempotency lock
    lock = None
    try:
        lock = acquire_video_lock(cfg.LOCK_DIR, int(video_id), int(cfg.LOCK_STALE_SECONDS))
    except LockBusyError:
        logger.info("lock busy video_id=%s", video_id)
        return

    hb: Optional[HeartbeatThread] = None
    try:
        # heartbeat (best-effort)
        hb = HeartbeatThread(
            client=client,
            video_id=int(video_id),
            interval=int(cfg.HEARTBEAT_INTERVAL_SECONDS),
            backoff_base=int(cfg.BACKOFF_BASE_SECONDS) if int(cfg.BACKOFF_BASE_SECONDS) > 0 else 1,
            backoff_cap=int(cfg.BACKOFF_CAP_SECONDS) if int(cfg.BACKOFF_CAP_SECONDS) > 0 else 10,
        )
        hb.start()

        # source url
        source_url = _build_source_url_from_file_key(file_key=file_key)

        with temp_workdir(cfg.TEMP_DIR, prefix=f"video-{video_id}-") as wd:
            wd = Path(wd)

            src_path = wd / "source.mp4"
            out_dir = wd / "hls"

            # 1) download source
            download_to_file(url=source_url, dst=src_path, cfg=cfg)

            # 2) duration (local ffprobe)
            duration = probe_duration_seconds(
                input_path=str(src_path),
                ffprobe_bin=cfg.FFPROBE_BIN,
                timeout=int(cfg.FFPROBE_TIMEOUT_SECONDS),
            )
            if not duration or duration <= 0:
                raise RuntimeError("duration_probe_failed")

            # 3) transcode -> hls
            transcode_to_hls(
                video_id=int(video_id),
                input_path=str(src_path),
                output_root=out_dir,
                ffmpeg_bin=cfg.FFMPEG_BIN,
                ffprobe_bin=cfg.FFPROBE_BIN,
                hls_time=int(cfg.HLS_TIME_SECONDS),
                timeout=int(cfg.FFMPEG_TIMEOUT_SECONDS),
            )

            # 4) validate
            validate_hls_output(out_dir, int(cfg.MIN_SEGMENTS_PER_VARIANT))

            # 5) thumbnail (midpoint)
            try:
                at = float(cfg.THUMBNAIL_AT_SECONDS)
                if duration >= 10:
                    at = float(int(duration * 0.5))
                elif duration >= 3:
                    at = float(max(1, duration // 2))
                else:
                    at = 0.0

                thumb_path = out_dir / "thumbnail.jpg"
                generate_thumbnail(
                    input_path=str(src_path),
                    output_path=thumb_path,
                    ffmpeg_bin=cfg.FFMPEG_BIN,
                    at_seconds=float(at),
                    timeout=min(int(cfg.FFMPEG_TIMEOUT_SECONDS), 120),
                )
            except Exception as e:
                logger.warning("thumbnail failed video_id=%s err=%s", video_id, e)

            # 6) upload directory to R2
            upload_directory(
                local_dir=out_dir,
                bucket=cfg.R2_BUCKET,
                prefix=_hls_r2_prefix(cfg, int(video_id)),
                endpoint_url=cfg.R2_ENDPOINT,
                access_key=cfg.R2_ACCESS_KEY,
                secret_key=cfg.R2_SECRET_KEY,
                region=cfg.R2_REGION,
                max_concurrency=int(cfg.UPLOAD_MAX_CONCURRENCY),
                retry_max=int(cfg.RETRY_MAX_ATTEMPTS),
                backoff_base=float(cfg.BACKOFF_BASE_SECONDS),
                backoff_cap=float(cfg.BACKOFF_CAP_SECONDS),
            )

        # 7) notify backend complete
        client.notify_complete(
            int(video_id),
            {
                "hls_path": _hls_master_relpath(cfg, int(video_id)),
                "duration": int(duration),
            },
        )

        logger.info("job completed video_id=%s duration=%s", video_id, duration)

    except Exception as e:
        logger.exception("job failed video_id=%s err=%s", video_id, e)
        try:
            client.notify_fail(int(video_id), str(e))
        except Exception:
            logger.exception("notify_fail failed video_id=%s", video_id)
        raise

    finally:
        try:
            if hb is not None:
                hb.stop()
        except Exception:
            pass
        try:
            if lock is not None:
                release_video_lock(lock)
        except Exception:
            pass
