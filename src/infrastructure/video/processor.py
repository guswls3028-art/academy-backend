"""
VideoProcessor - 실제 비디오 처리 (다운로드, 트랜스코딩, R2 업로드)

진행률은 IProgress에 기록 (Write-Behind, Redis 우선).
완료는 호출부(Handler)에서 repo.complete_video() 호출.

R2 raw 삭제: Lifecycle만 믿지 않고, 인코딩 성공 직후 반드시 삭제.
  → 구현 위치: 워커 성공 콜백 (apps/worker/video_worker/sqs_main.py).
  → 순서: HLS 업로드 완료(process_video) → DB 상태 '완료'(handler/repo.complete_video) → R2 raw_key 삭제(sqs_main).
  → 3시간 영상도 인코딩 직후 수 GB 즉시 반환.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.application.ports.progress import IProgress

logger = logging.getLogger(__name__)


def process_video(
    *,
    job: dict,
    cfg: Any,
    progress: IProgress,
) -> tuple[str, int]:
    """
    비디오 처리: 다운로드 -> 트랜스코드 -> R2 업로드

    Returns:
        (hls_master_path, duration_seconds)
    """
    from apps.worker.video_worker.download import download_to_file
    from apps.worker.video_worker.utils import temp_workdir, trim_tail
    from apps.worker.video_worker.video.duration import probe_duration_seconds
    from apps.worker.video_worker.video.thumbnail import generate_thumbnail
    from apps.worker.video_worker.video.transcoder import transcode_to_hls
    from apps.worker.video_worker.video.validate import validate_hls_output
    from apps.worker.video_worker.video.r2_uploader import upload_directory
    from libs.s3_client.presign import create_presigned_get_url

    video_id = int(job.get("video_id"))
    file_key = str(job.get("file_key") or "")
    tenant_id = job.get("tenant_id")
    if tenant_id is not None:
        tenant_id = int(tenant_id)
    job_id = f"video:{video_id}"

    if not video_id or tenant_id is None:
        raise ValueError("video_id and tenant_id required")

    progress.record_progress(job_id, "presigning", {"percent": 5})
    try:
        source_url = create_presigned_get_url(key=file_key, expires_in=600)
    except Exception as e:
        raise RuntimeError(f"presigned_get_failed:{trim_tail(str(e))}") from e

    from apps.core.r2_paths import video_hls_prefix, video_hls_master_path

    hls_prefix = video_hls_prefix(tenant_id=tenant_id, video_id=video_id)
    hls_master_path = video_hls_master_path(tenant_id=tenant_id, video_id=video_id)

    with temp_workdir(cfg.TEMP_DIR, prefix=f"video-{video_id}-") as wd:
        wd = Path(wd)
        src_path = wd / "source.mp4"
        out_dir = wd / "hls"

        progress.record_progress(job_id, "downloading", {"file_key": file_key, "percent": 15})
        download_to_file(url=source_url, dst=src_path, cfg=cfg)

        progress.record_progress(job_id, "probing", {"percent": 25})
        duration = probe_duration_seconds(
            input_path=str(src_path),
            ffprobe_bin=cfg.FFPROBE_BIN,
            timeout=int(cfg.FFPROBE_TIMEOUT_SECONDS),
        )
        if not duration or duration <= 0:
            raise RuntimeError("duration_probe_failed")

        progress.record_progress(job_id, "transcoding", {"duration": duration, "percent": 50})
        transcode_to_hls(
            video_id=video_id,
            input_path=str(src_path),
            output_root=out_dir,
            ffmpeg_bin=cfg.FFMPEG_BIN,
            ffprobe_bin=cfg.FFPROBE_BIN,
            hls_time=int(cfg.HLS_TIME_SECONDS),
            timeout=int(cfg.FFMPEG_TIMEOUT_SECONDS),
        )

        progress.record_progress(job_id, "validating")
        validate_hls_output(out_dir, int(cfg.MIN_SEGMENTS_PER_VARIANT))

        progress.record_progress(job_id, "thumbnail")
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

        progress.record_progress(job_id, "uploading", {"hls_prefix": hls_prefix})
        upload_directory(
            local_dir=out_dir,
            bucket=cfg.R2_BUCKET,
            prefix=hls_prefix,
            endpoint_url=cfg.R2_ENDPOINT,
            access_key=cfg.R2_ACCESS_KEY,
            secret_key=cfg.R2_SECRET_KEY,
            region=cfg.R2_REGION,
            max_concurrency=int(cfg.UPLOAD_MAX_CONCURRENCY),
            retry_max=int(cfg.RETRY_MAX_ATTEMPTS),
            backoff_base=float(cfg.BACKOFF_BASE_SECONDS),
            backoff_cap=float(cfg.BACKOFF_CAP_SECONDS),
        )

    progress.record_progress(job_id, "done", {"hls_path": hls_master_path, "duration": duration})
    return hls_master_path, int(duration)
