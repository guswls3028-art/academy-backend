"""
VideoProcessor - 실제 비디오 처리 (다운로드, 트랜스코딩, R2 업로드)

진행률은 IProgress 에 기록 (Write-Behind, Redis 우선).
완료(`Video.status = READY`)는 호출부 worker entry 가 repositories_video.job_complete 로 처리.

R2 raw 삭제 정책: 인코딩 성공 직후 즉시 삭제하지 않고, `purge_raw_videos` cron(매일 18:00)이
3일 경과한 raw 객체를 일괄 정리. 학원장이 인코딩 직후 원본을 다시 받아야 하는 운영 케이스를
허용하기 위한 의도된 지연. 자세한 책임 분담은 `backend/docs/infrastructure/video-cron-jobs.md`.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from academy.application.ports.progress import IProgress
from academy.application.video import CancelledError

logger = logging.getLogger(__name__)


def _check_abort(job: dict) -> None:
    """재시도로 취소 요청이 들어오면 중단 (handler가 skip 처리).

    `_cancel_check` 콜백은 통상 DB를 조회한다. 장시간 ffmpeg 직후 호출되면 메인 스레드의
    Django 커넥션이 RDS Proxy IdleClientTimeout(30분)에 의해 닫혀있어 OperationalError가
    발생한다. close_old_connections()로 stale 커넥션을 회수해 다음 쿼리에서 새로 연다.
    """
    try:
        from django.db import close_old_connections
        close_old_connections()
    except Exception:
        pass
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
) -> tuple[str, int, str]:
    """
    비디오 처리: 다운로드 -> 트랜스코드 -> R2 업로드

    Returns:
        (hls_master_path, duration_seconds, thumbnail_r2_key)
        - thumbnail_r2_key 는 항상 채워진다. 실패 시 RuntimeError raise.
    """
    from academy.adapters.video.downloader import download_to_file
    from academy.adapters.video.utils import temp_workdir, trim_tail
    from academy.adapters.video.duration import probe_duration_seconds
    from academy.adapters.video.thumbnail import generate_thumbnail
    from academy.adapters.video.transcoder import transcode_to_hls
    from academy.adapters.video.validate import validate_hls_output
    from academy.adapters.video.r2_uploader import upload_directory
    from libs.r2_client.presign import create_presigned_get_url

    video_id = int(job.get("video_id"))
    file_key = str(job.get("file_key") or "")
    tenant_id = job.get("tenant_id")
    if tenant_id is not None:
        tenant_id = int(tenant_id)
    job_id = f"video:{video_id}"

    if not video_id or tenant_id is None:
        raise ValueError("video_id and tenant_id required")

    _check_abort(job)

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
        source_url = create_presigned_get_url(key=file_key, expires_in=int(cfg.PRESIGN_GET_EXPIRES_SECONDS))
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

    from apps.core.r2_paths import video_hls_prefix, video_hls_master_path, video_hls_tmp_prefix

    hls_prefix = video_hls_prefix(tenant_id=tenant_id, video_id=video_id)
    hls_master_path = video_hls_master_path(tenant_id=tenant_id, video_id=video_id)
    job_id_str = (job.get("_job_id") or job.get("job_id") or "").strip() or str(video_id)
    hls_tmp_prefix = video_hls_tmp_prefix(tenant_id=tenant_id, video_id=video_id, job_id=job_id_str)

    with temp_workdir(cfg.TEMP_DIR, prefix=f"video-{video_id}-") as wd:
        wd = Path(wd)
        src_path = wd / "source.mp4"
        out_dir = wd / "hls"

        _check_abort(job)
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

        _check_abort(job)
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

        _check_abort(job)
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
        _check_abort(job)
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
            job_id=job.get("_job_id"),
            cancel_event=job.get("_cancel_event"),
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

        _check_abort(job)
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
        # 썸네일은 invariant: 실패 시 영상 처리 자체를 실패로 간주.
        # 모바일/카드 UI가 thumbnail 없는 영상을 "처리안됨"으로 인식하므로
        # status=READY 인데 thumbnail 없는 상태가 절대 만들어지면 안 된다.
        at = float(cfg.THUMBNAIL_AT_SECONDS)
        if duration >= 10:
            at = float(int(duration * 0.5))
        elif duration >= 3:
            at = float(max(1, duration // 2))
        else:
            at = 0.0

        thumb_path = out_dir / "thumbnail.jpg"
        try:
            generate_thumbnail(
                input_path=str(src_path),
                output_path=thumb_path,
                ffmpeg_bin=cfg.FFMPEG_BIN,
                at_seconds=float(at),
                timeout=min(int(cfg.FFMPEG_TIMEOUT_SECONDS), 120),
            )
        except Exception as e:
            raise RuntimeError(f"thumbnail_generation_failed:{trim_tail(str(e))}") from e
        if not thumb_path.exists() or thumb_path.stat().st_size <= 0:
            raise RuntimeError("thumbnail_generation_failed:empty_output")
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

        _check_abort(job)
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
        import time as _time_mod
        _upload_started_at = _time_mod.monotonic()
        _last_upload_log = [0.0]

        def _upload_progress(uploaded: int, total: int) -> None:
            now = _time_mod.monotonic()
            # 매 0.5초 또는 5% 단위로만 갱신 (Redis write 부담 최소화)
            if (now - _last_upload_log[0]) < 0.5 and uploaded != total:
                return
            _last_upload_log[0] = now
            elapsed = now - _upload_started_at
            rate = uploaded / elapsed if elapsed > 0 else 0
            remaining_files = max(0, total - uploaded)
            remaining_sec = int(remaining_files / rate) if rate > 0 else 30
            step_pct = int(100 * uploaded / total) if total > 0 else 100
            step_pct = min(100, max(0, step_pct))
            overall = int(95 + 5 * uploaded / total) if total > 0 else 100
            overall = min(100, max(95, overall))
            progress.record_progress(
                job_id,
                "uploading",
                {
                    "hls_prefix": hls_prefix,
                    "percent": overall,
                    "remaining_seconds": remaining_sec,
                    "step_index": 7,
                    "step_total": VIDEO_ENCODING_STEP_TOTAL,
                    "step_name": "uploading",
                    "step_name_display": "업로드",
                    "step_percent": step_pct,
                    "uploaded_files": uploaded,
                    "total_files": total,
                },
                tenant_id=tenant_id_str,
            )

        upload_directory(
            local_dir=out_dir,
            bucket=cfg.R2_BUCKET,
            prefix=hls_tmp_prefix,
            endpoint_url=cfg.R2_ENDPOINT,
            access_key=cfg.R2_ACCESS_KEY,
            secret_key=cfg.R2_SECRET_KEY,
            region=cfg.R2_REGION,
            max_concurrency=int(cfg.UPLOAD_MAX_CONCURRENCY),
            retry_max=int(cfg.RETRY_MAX_ATTEMPTS),
            backoff_base=float(cfg.BACKOFF_BASE_SECONDS),
            backoff_cap=float(cfg.BACKOFF_CAP_SECONDS),
            progress_callback=_upload_progress,
        )
        from academy.adapters.video.r2_uploader import (
            publish_tmp_to_final,
            verify_hls_integrity_r2,
            delete_prefix,
            UploadIntegrityError,
        )
        try:
            publish_tmp_to_final(
                bucket=cfg.R2_BUCKET,
                tmp_prefix=hls_tmp_prefix,
                final_prefix=hls_prefix,
                endpoint_url=cfg.R2_ENDPOINT,
                access_key=cfg.R2_ACCESS_KEY,
                secret_key=cfg.R2_SECRET_KEY,
                region=cfg.R2_REGION,
            )
            verify_hls_integrity_r2(
                bucket=cfg.R2_BUCKET,
                final_prefix=hls_prefix,
                endpoint_url=cfg.R2_ENDPOINT,
                access_key=cfg.R2_ACCESS_KEY,
                secret_key=cfg.R2_SECRET_KEY,
                region=cfg.R2_REGION,
                min_segments=max(3, int(getattr(cfg, "MIN_SEGMENTS_PER_VARIANT", 3))),
            )
        except UploadIntegrityError as e:
            from apps.domains.video.services.ops_events import emit_ops_event
            emit_ops_event(
                "UPLOAD_INTEGRITY_FAIL",
                severity="ERROR",
                tenant_id=tenant_id,
                video_id=video_id,
                job_id=job_id_str,
                payload={"reason": str(e)[:500]},
            )
            delete_prefix(
                bucket=cfg.R2_BUCKET,
                prefix=hls_prefix,
                endpoint_url=cfg.R2_ENDPOINT,
                access_key=cfg.R2_ACCESS_KEY,
                secret_key=cfg.R2_SECRET_KEY,
                region=cfg.R2_REGION,
            )
            raise
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
    # thumbnail.jpg 는 hls 디렉토리와 함께 final prefix 로 publish 되었다.
    thumbnail_r2_key = f"{hls_prefix.rstrip('/')}/thumbnail.jpg"
    return hls_master_path, int(duration), thumbnail_r2_key
