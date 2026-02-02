# PATH: apps/support/video/services/queue.py
from __future__ import annotations

from typing import Optional
from django.db import transaction

from apps.support.video.models import Video
from apps.support.video.constants import VideoStatus


class VideoJobQueue:
    """
    AI worker의 DBJobQueue와 동일한 철학:
    - DB가 단일 진실
    - claim은 원자적
    - job이 없으면 None 반환
    """

    @classmethod
    @transaction.atomic
    def claim_next(cls, worker_id: str) -> Optional[Video]:
        """
        처리 대기 중인 Video 하나를 claim 한다.
        """
        video = (
            Video.objects
            .select_for_update(skip_locked=True)
            .filter(status=VideoStatus.UPLOADED)
            .order_by("created_at")
            .first()
        )

        if not video:
            return None

        video.status = VideoStatus.PROCESSING
        video.processing_worker_id = worker_id
        video.save(update_fields=["status", "processing_worker_id"])

        return video
