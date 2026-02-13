"""
VideoRepository - IVideoRepository 구현체

Django ORM을 사용하여 Video 상태 업데이트.
Worker는 모델을 직접 부르지 않고 repo.mark_processing(), repo.complete_video() 등만 호출.
"""
from __future__ import annotations

import logging
from typing import Optional

from django.db import transaction
from django.utils import timezone

from src.application.ports.video_repository import IVideoRepository
from apps.support.video.models import Video

logger = logging.getLogger(__name__)


class VideoRepository(IVideoRepository):
    """IVideoRepository 구현 (Django ORM)"""

    @transaction.atomic
    def mark_processing(self, video_id: int) -> bool:
        video = Video.objects.select_for_update().filter(id=int(video_id)).first()
        if not video:
            return False

        if video.status == Video.Status.PROCESSING:
            return True

        if video.status != Video.Status.UPLOADED:
            logger.warning(
                "Cannot mark video %s as PROCESSING: status=%s",
                video_id,
                video.status,
            )
            return False

        video.status = Video.Status.PROCESSING
        if hasattr(video, "processing_started_at"):
            video.processing_started_at = timezone.now()

        update_fields = ["status"]
        if hasattr(video, "processing_started_at"):
            update_fields.append("processing_started_at")

        video.save(update_fields=update_fields)
        return True

    @transaction.atomic
    def complete_video(
        self,
        video_id: int,
        hls_path: str,
        duration: Optional[int] = None,
    ) -> tuple[bool, str]:
        video = Video.objects.select_for_update().filter(id=int(video_id)).first()
        if not video:
            return False, "not_found"

        if video.status == Video.Status.READY and bool(video.hls_path):
            return True, "idempotent"

        if video.status != Video.Status.PROCESSING:
            logger.warning(
                "Video %s status is %s (expected PROCESSING)",
                video_id,
                video.status,
            )

        video.hls_path = str(hls_path)
        if duration is not None and duration >= 0:
            video.duration = int(duration)
        video.status = Video.Status.READY

        if hasattr(video, "leased_until"):
            video.leased_until = None
        if hasattr(video, "leased_by"):
            video.leased_by = ""

        update_fields = ["hls_path", "status"]
        if duration is not None and duration >= 0:
            update_fields.append("duration")
        if hasattr(video, "leased_until"):
            update_fields.append("leased_until")
        if hasattr(video, "leased_by"):
            update_fields.append("leased_by")

        video.save(update_fields=update_fields)
        return True, "ok"

    @transaction.atomic
    def fail_video(self, video_id: int, reason: str) -> tuple[bool, str]:
        video = Video.objects.select_for_update().filter(id=int(video_id)).first()
        if not video:
            return False, "not_found"

        if video.status == Video.Status.FAILED:
            return True, "idempotent"

        video.status = Video.Status.FAILED
        if hasattr(video, "error_reason"):
            video.error_reason = str(reason)[:2000]

        if hasattr(video, "leased_until"):
            video.leased_until = None
        if hasattr(video, "leased_by"):
            video.leased_by = ""

        update_fields = ["status"]
        if hasattr(video, "error_reason"):
            update_fields.append("error_reason")
        if hasattr(video, "leased_until"):
            update_fields.append("leased_until")
        if hasattr(video, "leased_by"):
            update_fields.append("leased_by")

        video.save(update_fields=update_fields)
        return True, "ok"
