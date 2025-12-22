from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from celery import shared_task
from django.conf import settings
from django.db import transaction

from apps.support.media.models import Video
from libs.s3_client.presign import create_presigned_put_url
from apps.worker.media.video import processor
from apps.worker.media.video.processor import MediaProcessingError

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
        # fallback (should not normally happen)
        return str(path)


# ---------------------------------------------------------------------
# Celery Task
# ---------------------------------------------------------------------

@shared_task(
    bind=True,
    queue="video",   # ✅ 이거 추가
    autoretry_for=(),   # retry 판단 ❌ (의도적으로 비활성)
    retry_backoff=False,
    retry_kwargs=None,
)
def process_video_media(self, video_id: int) -> None:
    """
    Orchestrates media processing for a single Video.

    Responsibilities:
    - DB lock
    - status transition
    - calling processor
    - persisting results
    """

    # 1) Lock & initial state check
    with transaction.atomic():
        video = (
            Video.objects
            .select_for_update()
            .filter(id=video_id)
            .first()
        )

        if video is None:
            logger.warning(
                "[media] Video not found (video_id=%s)", video_id
            )
            return

        if video.status != Video.Status.UPLOADED:
            # idempotency guard
            logger.info(
                "[media] Skip processing due to status=%s (video_id=%s)",
                video.status,
                video_id,
            )
            return

        video.status = Video.Status.PROCESSING
        video.save(update_fields=["status"])

    # 2) Build input/output contracts (outside DB lock)
    try:
        input_url = generate_presigned_get_url(
            bucket=video.s3_bucket,
            key=video.s3_key,
            expires_in=60 * 60,  # 충분히 긴 TTL
        )

        output_root = _get_hls_output_root(video_id)

    except Exception as e:
        # Presign 실패는 즉시 FAILED
        logger.exception(
            "[media] Failed to prepare input/output (video_id=%s)",
            video_id,
        )
        _mark_failed(video_id)
        return

    # 3) Run processor (actual work)
    try:
        result = processor.run(
            video_id=video_id,
            input_url=input_url,
            output_root=output_root,
        )

    except MediaProcessingError as e:
        # 의미 있는 실패 (stage/code/context 포함)
        logger.error(
            "[media] Media processing failed (video_id=%s) %s",
            video_id,
            e.to_dict(),
        )
        _mark_failed(video_id)
        return

    except Exception as e:
        # 예상 못 한 실패 (버그/환경 문제)
        logger.exception(
            "[media] Unexpected error during media processing (video_id=%s)",
            video_id,
        )
        _mark_failed(video_id)
        return

    # 4) Persist results & mark READY
    with transaction.atomic():
        video = (
            Video.objects
            .select_for_update()
            .filter(id=video_id)
            .first()
        )

        if video is None:
            # 매우 드문 케이스: 처리 중 삭제
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

    logger.info(
        "[media] Video media processing READY (video_id=%s)",
        video_id,
    )


# ---------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------

def _mark_failed(video_id: int) -> None:
    """
    Mark Video as FAILED. No retry decision here.
    """
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
