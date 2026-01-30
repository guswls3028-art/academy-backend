from __future__ import annotations
# ⚠️ 반드시 최상단 (import 순서 중요)

# apps/shared/tasks/media.py
print("[media] task module imported")

import logging
from pathlib import Path

import requests
from celery import shared_task
from django.conf import settings
from django.db import transaction

from libs.s3_client.presign import create_presigned_get_url

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _get_hls_output_root(video_id: int) -> Path:
    """
    storage/media/hls/videos/{video_id}
    """
    return (
        Path(settings.BASE_DIR)
        / "storage"
        / "media"
        / "hls"
        / "videos"
        / str(video_id)
    )


def _to_relative_media_path(path: Path) -> str:
    """
    Convert absolute path under BASE_DIR/storage to relative media path.
    """
    base = Path(settings.BASE_DIR)
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def notify_processing_complete(
    *,
    video_id: int,
    hls_path: str,
    duration: int | None,
) -> None:
    """
    Video Worker -> API ACK
    """
    url = f"{settings.API_BASE_URL}/api/v1/internal/videos/{video_id}/processing-complete/"

    headers = {
        "X-Worker-Token": settings.INTERNAL_WORKER_TOKEN,
        "Content-Type": "application/json",
    }

    payload = {
        "hls_path": hls_path,
        "duration": duration,
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=5)
    resp.raise_for_status()


# ---------------------------------------------------------------------
# Celery Task (VIDEO QUEUE 전용)
# ---------------------------------------------------------------------

@shared_task(
    bind=True,
    queue="video",
    autoretry_for=(),
    retry_backoff=False,
)
def process_video_media(self, video_id: int) -> None:
    """
    Video media processing task.
    ⚠️ 이 task는 '비디오 워커 서버'에서만 정상 실행됨
    """

    # ✅ 중요: 비디오 워커 전용 import (API 서버 보호)
    from apps.worker.video_worker.video.processor import (
        run as run_processor,
        MediaProcessingError,
    )
    from apps.worker.video_worker.r2_uploader import upload_dir
    from apps.support.media.models import Video

    logger.info("[media] Start processing (video_id=%s)", video_id)

    # 1️⃣ Lock & 상태 전이
    with transaction.atomic():
        video = (
            Video.objects
            .select_for_update()
            .filter(id=video_id)
            .first()
        )

        if video is None:
            logger.warning("[media] Video not found (video_id=%s)", video_id)
            return

        if video.status != Video.Status.UPLOADED:
            logger.info(
                "[media] Skip processing (status=%s, video_id=%s)",
                video.status,
                video_id,
            )
            return

        video.status = Video.Status.PROCESSING
        video.save(update_fields=["status"])

    # 2️⃣ 입력 URL + 출력 경로
    try:
        input_url = create_presigned_get_url(
            key=video.file_key,
            expires_in=60 * 60,
        )
        output_root = _get_hls_output_root(video_id)

    except Exception:
        logger.exception(
            "[media] Failed to prepare input/output (video_id=%s)",
            video_id,
        )
        _mark_failed(video_id)
        return

    # 3️⃣ ffmpeg + HLS 처리
    try:
        result = run_processor(
            video_id=video_id,
            input_url=input_url,
            output_root=output_root,
        )

    except MediaProcessingError as e:
        logger.error(
            "[media] Media processing failed (video_id=%s) %s",
            video_id,
            e.to_dict(),
        )
        _mark_failed(video_id)
        return

    except Exception:
        logger.exception(
            "[media] Unexpected error during media processing (video_id=%s)",
            video_id,
        )
        _mark_failed(video_id)
        return

    # 4️⃣ DB 반영 (READY)
    with transaction.atomic():
        video = (
            Video.objects
            .select_for_update()
            .filter(id=video_id)
            .first()
        )

        if video is None:
            logger.warning(
                "[media] Video disappeared before READY persist (video_id=%s)",
                video_id,
            )
            return

        video.duration = result.duration_seconds
        video.thumbnail = _to_relative_media_path(result.thumbnail_path)
        video.hls_path = _to_relative_media_path(result.master_playlist_path)
        video.status = Video.Status.READY

        video.save(
            update_fields=[
                "duration",
                "thumbnail",
                "hls_path",
                "status",
            ]
        )

    # 5️⃣ R2 업로드 (트랜잭션 밖)
    try:
        upload_dir(
            local_dir=output_root,
            prefix=f"media/hls/videos/{video_id}",
        )
    except Exception:
        logger.exception(
            "[media] R2 upload failed (video_id=%s)",
            video_id,
        )
        _mark_failed(video_id)
        return

    logger.info(
        "[media] Video media processing READY (video_id=%s)",
        video_id,
    )

    # 6️⃣ API 통지 (실패해도 READY 유지)
    try:
        notify_processing_complete(
            video_id=video_id,
            hls_path=str(video.hls_path),
            duration=video.duration,
        )
        logger.info(
            "[media] Video processing notified API (video_id=%s)",
            video_id,
        )
    except Exception:
        logger.exception(
            "[media] Failed to notify API (video_id=%s)",
            video_id,
        )


# ---------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------

def _mark_failed(video_id: int) -> None:
    """
    Mark Video as FAILED.
    """
    from apps.support.media.models import Video

    with transaction.atomic():
        video = (
            Video.objects
            .select_for_update()
            .filter(id=video_id)
            .first()
        )
        if video is None:
            return

        video.status = Video.Status.FAILED
        video.save(update_fields=["status"])
