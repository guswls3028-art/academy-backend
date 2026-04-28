"""
delete_r2 전용 SQS. academy-video-delete-r2 큐 사용.

Encoding과 완전 분리. Encoding = Batch ONLY.
"""

from __future__ import annotations

import logging
from django.conf import settings
from django.utils import timezone
from libs.queue import get_queue_client

logger = logging.getLogger(__name__)

QUEUE_NAME = "academy-video-delete-r2"


def _get_queue_name() -> str:
    return getattr(settings, "VIDEO_SQS_QUEUE_DELETE_R2", QUEUE_NAME)


def enqueue_delete_r2(
    *,
    tenant_id: int,
    video_id: int,
    file_key: str,
    hls_prefix: str,
) -> bool:
    """영상 삭제 후 R2 정리. SQS academy-video-delete-r2 → Lambda."""
    message = {
        "action": "delete_r2",
        "tenant_id": tenant_id,
        "video_id": video_id,
        "file_key": (file_key or "").strip(),
        "hls_prefix": hls_prefix,
        "created_at": timezone.now().isoformat(),
    }
    try:
        client = get_queue_client()
        success = client.send_message(queue_name=_get_queue_name(), message=message)
        if success:
            logger.info("R2 delete job enqueued: video_id=%s hls_prefix=%s", video_id, hls_prefix)
        return bool(success)
    except Exception as e:
        logger.exception("Error enqueuing R2 delete job: video_id=%s, error=%s", video_id, e)
        return False
