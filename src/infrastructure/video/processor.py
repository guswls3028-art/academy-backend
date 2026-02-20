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
from src.application.video.handler import CancelledError

logger = logging.getLogger(__name__)


def _check_abort(job: dict) -> None:
    """재시도로 취소 요청이 들어오면 중단 (handler가 skip 처리)."""
    check = job.get("_cancel_check")
    if check and callable(check) and check():
        raise CancelledError("Retry requested; aborting current job")

# 구간별 진행률 (n/7): 업로드 마법사처럼 단계별 0~100% 제공
VIDEO_ENCODING_STEP_TOTAL = 7
VIDEO_ENCODING_STEPS = [
    (1, "presigning", "준비"),
    (2, "downloading", "다운로드"),
    (3, "probing", "분석"),
    (4, "transcoding", "인코딩"),
    (5, "validating", "검증"),
    (6, "thumbnail", "썸네일"),
    (7, "uploading", "업로드"),
]


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

    # ✅ tenant_id를 문자열로 변환하여 전달 (Redis 키 형식 일치)
    tenant_id_str = str(tenant_id)

    # 남은 시간 예상: presigning 시점에선 duration 모름 → 다운로드 후 갱신
    progress.record_progress(
        job_id,
        "presigning",
        {
            "percent": 5,
            "remaining_seconds": 120,
            "step_index": 1,
            "step_total": VIDEO_ENCODING_STEP_TOTAL,
            "step_name": "presigning",
            "step_name_display": "준비",
            "step_percent": 0,  # ✅ 단계 시작: 0%
        },
        tenant_id=tenant_id_str,  # ✅ tenant_id 전달 추가
    )
    try:
        source_url = create_presigned_get_url(key=file_key, expires_in=600)
        # ✅ 단계 완료: 100%
        progress.record_progress(
            job_id,
            "presigning",
            {
                "percent": 5,
                "remaining_seconds": 120,
                "step_index": 1,
                "step_total": VIDEO_ENCODING_STEP_TOTAL,
                "step_name": "presigning",
                "step_name_display": "준비",
                "step_percent": 100,
            },
            tenant_id=tenant_id_str,
        )
    except Exception as e:
        raise RuntimeError(f"presigned_get_failed:{trim_tail(str(e))}") from e

    from apps.core.r2_paths import video_hls_prefix, video_hls_master_path

    hls_prefix = video_hls_prefix(tenant_id=tenant_id, video_id=video_id)
    hls_master_path = video_hls_master_path(tenant_id=tenant_id, video_id=video_id)

    with temp_workdir(cfg.TEMP_DIR, prefix=f"video-{video_id}-") as wd:
        wd = Path(wd)
        src_path = wd / "source.mp4"
        out_dir = wd / "hls"

        progress.record_progress(
            job_id,
            "downloading",
            {
                "file_key": file_key,
                "percent": 15,
                "remaining_seconds": 300,
                "step_index": 2,
                "step_total": VIDEO_ENCODING_STEP_TOTAL,
                "step_name": "downloading",
                "step_name_display": "다운로드",
                "step_percent": 0,  # ✅ 단계 시작: 0%
            },
            tenant_id=tenant_id_str,  # ✅ tenant_id 전달 추가
        )
        download_to_file(url=source_url, dst=src_path, cfg=cfg)
        # ✅ 단계 완료: 100%
        progress.record_progress(
            job_id,
            "downloading",
            {
                "file_key": file_key,
                "percent": 15,
                "remaining_seconds": 300,
                "step_index": 2,
                "step_total": VIDEO_ENCODING_STEP_TOTAL,
                "step_name": "downloading",
                "step_name_display": "다운로드",
                "step_percent": 100,
            },
            tenant_id=tenant_id_str,
        )

        progress.record_progress(
            job_id,
            "probing",
            {
                "percent": 25,
                "remaining_seconds": 240,
                "step_index": 3,
                "step_total": VIDEO_ENCODING_STEP_TOTAL,
                "step_name": "probing",
                "step_name_display": "분석",
                "step_percent": 0,  # ✅ 단계 시작: 0%
            },
            tenant_id=tenant_id_str,  # ✅ tenant_id 전달 추가
        )
        duration = probe_duration_seconds(
            input_path=str(src_path),
            ffprobe_bin=cfg.FFPROBE_BIN,
            timeout=int(cfg.FFPROBE_TIMEOUT_SECONDS),
        )
        if not duration or duration <= 0:
            raise RuntimeError("duration_probe_failed")
        # ✅ 단계 완료: 100%
        progress.record_progress(
            job_id,
            "probing",
            {
                "percent": 25,
                "remaining_seconds": 240,
                "step_index": 3,
                "step_total": VIDEO_ENCODING_STEP_TOTAL,
                "step_name": "probing",
                "step_name_display": "분석",
                "step_percent": 100,
            },
            tenant_id=tenant_id_str,
        )

        # 트랜스코딩: 구간 내 0~100% (인코딩 단계만 세부 진행률)
        transcode_started = False
        def transcode_progress(current_sec: float, total_sec: float) -> None:
            nonlocal transcode_started
            step_pct = int(100 * (current_sec / total_sec)) if total_sec > 0 else 0
            step_pct = min(100, max(0, step_pct))
            
            # ✅ 첫 호출 시 current_sec이 이미 진행된 상태면 0%를 먼저 업데이트
            if not transcode_started and current_sec > 0.5:  # 0.5초 이상 진행된 상태면
                progress.record_progress(
                    job_id,
                    "transcoding",
                    {
                        "duration": duration,
                        "percent": 50,
                        "remaining_seconds": int(total_sec + 60),
                        "current_sec": 0,
                        "step_index": 4,
                        "step_total": VIDEO_ENCODING_STEP_TOTAL,
                        "step_name": "transcoding",
                        "step_name_display": "인코딩",
                        "step_percent": 0,
                    },
                    tenant_id=tenant_id_str,
                )
                transcode_started = True
            
            pct = int(50 + 35 * (current_sec / total_sec)) if total_sec > 0 else 50
            pct = min(85, max(50, pct))
            post_sec = 60  # validate + thumbnail + upload 대략
            remaining = int(max(0, total_sec - current_sec + post_sec))
            logger.info(
                "[PROCESSOR] Transcode progress video_id=%s current=%.1f/%d step_percent=%d%% overall=%d%%",
                video_id, current_sec, int(total_sec), step_pct, pct,
            )
            progress.record_progress(
                job_id,
                "transcoding",
                {
                    "duration": duration,
                    "percent": pct,
                    "remaining_seconds": remaining,
                    "current_sec": int(current_sec),
                    "step_index": 4,
                    "step_total": VIDEO_ENCODING_STEP_TOTAL,
                    "step_name": "transcoding",
                    "step_name_display": "인코딩",
                    "step_percent": step_pct,
                },
                tenant_id=tenant_id_str,  # ✅ tenant_id 전달 추가
            )
            transcode_started = True

        progress.record_progress(
            job_id,
            "transcoding",
            {
                "duration": duration,
                "percent": 50,
                "remaining_seconds": int(duration + 60),
                "step_index": 4,
                "step_total": VIDEO_ENCODING_STEP_TOTAL,
                "step_name": "transcoding",
                "step_name_display": "인코딩",
                "step_percent": 0,
            },
            tenant_id=tenant_id_str,  # ✅ tenant_id 전달 추가
        )
        transcode_to_hls(
            video_id=video_id,
            input_path=str(src_path),
            output_root=out_dir,
            ffmpeg_bin=cfg.FFMPEG_BIN,
            ffprobe_bin=cfg.FFPROBE_BIN,
            hls_time=int(cfg.HLS_TIME_SECONDS),
            timeout=int(cfg.FFMPEG_TIMEOUT_SECONDS),
            duration_sec=duration,
            progress_callback=transcode_progress,
        )

        progress.record_progress(
            job_id,
            "validating",
            {
                "percent": 85,
                "remaining_seconds": 45,
                "step_index": 5,
                "step_total": VIDEO_ENCODING_STEP_TOTAL,
                "step_name": "validating",
                "step_name_display": "검증",
                "step_percent": 0,  # ✅ 단계 시작: 0%
            },
            tenant_id=tenant_id_str,  # ✅ tenant_id 전달 추가
        )
        validate_hls_output(out_dir, int(cfg.MIN_SEGMENTS_PER_VARIANT))
        # ✅ 단계 완료: 100%
        progress.record_progress(
            job_id,
            "validating",
            {
                "percent": 85,
                "remaining_seconds": 45,
                "step_index": 5,
                "step_total": VIDEO_ENCODING_STEP_TOTAL,
                "step_name": "validating",
                "step_name_display": "검증",
                "step_percent": 100,
            },
            tenant_id=tenant_id_str,
        )

        progress.record_progress(
            job_id,
            "thumbnail",
            {
                "percent": 90,
                "remaining_seconds": 30,
                "step_index": 6,
                "step_total": VIDEO_ENCODING_STEP_TOTAL,
                "step_name": "thumbnail",
                "step_name_display": "썸네일",
                "step_percent": 0,  # ✅ 단계 시작: 0%
            },
            tenant_id=tenant_id_str,  # ✅ tenant_id 전달 추가
        )
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
            # ✅ 단계 완료: 100%
            progress.record_progress(
                job_id,
                "thumbnail",
                {
                    "percent": 90,
                    "remaining_seconds": 30,
                    "step_index": 6,
                    "step_total": VIDEO_ENCODING_STEP_TOTAL,
                    "step_name": "thumbnail",
                    "step_name_display": "썸네일",
                    "step_percent": 100,
                },
                tenant_id=tenant_id_str,
            )
        except Exception as e:
            logger.warning("thumbnail failed video_id=%s err=%s", video_id, e)
            # 실패해도 100%로 표시 (다음 단계로 진행)
            progress.record_progress(
                job_id,
                "thumbnail",
                {
                    "percent": 90,
                    "remaining_seconds": 30,
                    "step_index": 6,
                    "step_total": VIDEO_ENCODING_STEP_TOTAL,
                    "step_name": "thumbnail",
                    "step_name_display": "썸네일",
                    "step_percent": 100,
                },
                tenant_id=tenant_id_str,
            )

        progress.record_progress(
            job_id,
            "uploading",
            {
                "hls_prefix": hls_prefix,
                "percent": 95,
                "remaining_seconds": 15,
                "step_index": 7,
                "step_total": VIDEO_ENCODING_STEP_TOTAL,
                "step_name": "uploading",
                "step_name_display": "업로드",
                "step_percent": 0,  # ✅ 단계 시작: 0%
            },
            tenant_id=tenant_id_str,  # ✅ tenant_id 전달 추가
        )
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
        # ✅ 단계 완료: 100%
        progress.record_progress(
            job_id,
            "uploading",
            {
                "hls_prefix": hls_prefix,
                "percent": 95,
                "remaining_seconds": 15,
                "step_index": 7,
                "step_total": VIDEO_ENCODING_STEP_TOTAL,
                "step_name": "uploading",
                "step_name_display": "업로드",
                "step_percent": 100,
            },
            tenant_id=tenant_id_str,
        )

    progress.record_progress(
        job_id,
        "done",
        {
            "hls_path": hls_master_path,
            "duration": duration,
            "percent": 100,
            "remaining_seconds": 0,
            "step_index": VIDEO_ENCODING_STEP_TOTAL,
            "step_total": VIDEO_ENCODING_STEP_TOTAL,
            "step_name": "done",
            "step_name_display": "완료",
            "step_percent": 100,
        },
        tenant_id=tenant_id_str,  # ✅ tenant_id 전달 추가
    )
    return hls_master_path, int(duration)
