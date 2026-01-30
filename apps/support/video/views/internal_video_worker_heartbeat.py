# PATH: apps/support/video/views/internal_video_worker_heartbeat.py

import logging
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from ..models import Video

logger = logging.getLogger("video.worker.heartbeat")


class InternalVideoWorkerHeartbeatView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, video_id: int):
        worker_id = request.headers.get("X-Worker-Id") or "worker-unknown"

        video = Video.objects.filter(id=video_id).first()
        if not video:
            return Response(status=status.HTTP_404_NOT_FOUND)

        # lease owner 검증
        leased_by = getattr(video, "leased_by", None)
        leased_until = getattr(video, "leased_until", None)

        if leased_by and leased_by != worker_id:
            logger.warning(
                "heartbeat rejected lease_owner_mismatch video_id=%s leased_by=%s from=%s",
                video_id, leased_by, worker_id,
            )
            return Response(status=status.HTTP_409_CONFLICT)

        if leased_until and leased_until < timezone.now():
            logger.warning(
                "heartbeat rejected lease_expired video_id=%s worker=%s",
                video_id, worker_id,
            )
            return Response(status=status.HTTP_409_CONFLICT)

        # heartbeat accept → lease 연장
        try:
            video.processing_started_at = video.processing_started_at or timezone.now()
            video.leased_by = worker_id
            video.leased_until = timezone.now() + timezone.timedelta(seconds=60)
            video.save(update_fields=["processing_started_at", "leased_by", "leased_until"])
        except Exception:
            pass

        logger.info(
            "heartbeat ok video_id=%s worker=%s",
            video_id, worker_id,
        )
        return Response({"ok": True})
