# PATH: apps/worker/video_worker/video/processor.py

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Any

from libs.s3_client.presign import create_presigned_get_url

from apps.worker.video_worker.config import Config
from apps.worker.video_worker.download import download_to_file
from apps.worker.video_worker.http_client import VideoAPIClient
from apps.worker.video_worker.locking import acquire_video_lock, release_video_lock
from apps.worker.video_worker.heartbeat import HeartbeatThread
from apps.worker.video_worker.video.r2_uploader import upload_directory
from apps.worker.video_worker.utils import temp_workdir, trim_tail

from apps.worker.video_worker.video.transcoder import transcode_to_hls
from apps.worker.video_worker.video.thumbnail import generate_thumbnail
from apps.worker.video_worker.video.validate import validate_hls_output
from apps.worker.video_worker.video.duration import probe_duration_seconds

logger = logging.getLogger("video_worker")


def process_video_job(*, job: Dict[str, Any], cfg: Config, client: VideoAPIClient) -> None:
    video_id = int(job["video_id"])
    file_key = str(job["file_key"])

    lock = acquire_video_lock(cfg.LOCK_DIR, video_id, cfg.LOCK_STALE_SECONDS)
    hb = HeartbeatThread(
        client=client,
        video_id=video_id,
        interval=cfg.HEARTBEAT_INTERVAL_SECONDS,
        backoff_base=cfg.BACKOFF_BASE_SECONDS,
        backoff_cap=cfg.BACKOFF_CAP_SECONDS,
    )

    try:
        hb.start()

        with temp_workdir(cfg.TEMP_DIR, f"video-{video_id}-") as workdir:
            src = Path(workdir) / "source.mp4"
            out = Path(workdir) / "out"
            out.mkdir(parents=True, exist_ok=True)

            source_url = create_presigned_get_url(key=file_key, expires_in=3600)
            download_to_file(url=source_url, dst=src, cfg=cfg)

            duration = probe_duration_seconds(
                input_path=str(src),
                ffprobe_bin=cfg.FFPROBE_BIN,
                timeout=min(cfg.FFPROBE_TIMEOUT_SECONDS, 30),
            )

            generate_thumbnail(
                input_path=str(src),
                output_path=out / "thumbnail.jpg",
                ffmpeg_bin=cfg.FFMPEG_BIN,
                at_seconds=cfg.THUMBNAIL_AT_SECONDS,
                timeout=min(cfg.FFMPEG_TIMEOUT_SECONDS, 180),
            )

            master = transcode_to_hls(
                video_id=video_id,
                input_path=str(src),
                output_root=out,
                ffmpeg_bin=cfg.FFMPEG_BIN,
                ffprobe_bin=cfg.FFPROBE_BIN,
                hls_time=cfg.HLS_TIME_SECONDS,
                timeout=cfg.FFMPEG_TIMEOUT_SECONDS,
            )

            validate_hls_output(out, cfg.MIN_SEGMENTS_PER_VARIANT)

            remote_prefix = f"{cfg.R2_PREFIX}/{video_id}"

            upload_directory(
                local_dir=out,
                bucket=cfg.R2_BUCKET,
                prefix=remote_prefix,
                endpoint_url=cfg.R2_ENDPOINT,
                access_key=cfg.R2_ACCESS_KEY,
                secret_key=cfg.R2_SECRET_KEY,
                region=cfg.R2_REGION,
                max_concurrency=cfg.UPLOAD_MAX_CONCURRENCY,
            )

            payload = {
                "hls_path": f"{remote_prefix}/{master.name}",
            }
            if duration is not None:
                payload["duration"] = duration

            client.notify_complete(video_id, payload)

    except Exception as e:
        reason = trim_tail(str(e), 2000)
        try:
            client.notify_fail(video_id, reason)
        except Exception:
            pass
        raise
    finally:
        hb.stop()
        release_video_lock(lock)
