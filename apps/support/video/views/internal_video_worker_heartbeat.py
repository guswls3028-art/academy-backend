# PATH: apps/support/video/views/internal_video_worker_heartbeat.py

from __future__ import annotations

import logging

from django.conf import settings
from django.http import JsonResponse

from rest_framework.views import APIView
from rest_framework.permissions import AllowAny

from apps.support.video.services.queue import VideoJobQueue


logger = logging.getLogger("video.worker.heartbeat")


def _require_worker_token(request) -> bool:
    expected = str(getattr(settings, "INTERNAL_WORKER_TOKEN", "") or "")
    if not expected:
        return False

    token = (
        request.headers.get("X-Worker-Token")
        or request.META.get("HTTP_X_WORKER_TOKEN")
        or ""
    )
    return str(token) == expected


def _worker_id(request) -> str:
    return (
        request.headers.get("X-Worker-Id")
        or request.headers.get("X-Worker-ID")
        or request.META.get("HTTP_X_WORKER_ID")
        or "worker-unknown"
    )


LEASE_SECONDS = int(getattr(settings, "VIDEO_WORKER_LEASE_SECONDS", 60))


class InternalVideoWorkerHeartbeatView(APIView):
    """
    POST /internal/video-worker/<video_id>/heartbeat/

    ✅ SSOT:
    - queue.heartbeat()가 lease 연장 단일 진입점
    - owner mismatch/lease expired면 409
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, video_id: int):
        if not _require_worker_token(request):
            expected = str(getattr(settings, "INTERNAL_WORKER_TOKEN", "") or "")
            if not expected:
                return JsonResponse(
                    {"detail": "INTERNAL_WORKER_TOKEN not configured"},
                    status=503,
                )
            return JsonResponse({"detail": "Unauthorized"}, status=401)

        wid = _worker_id(request)

        ok = VideoJobQueue.heartbeat(
            video_id=int(video_id),
            worker_id=wid,
            lease_seconds=LEASE_SECONDS,
        )

        if not ok:
            logger.warning(
                "heartbeat rejected video_id=%s worker=%s",
                str(video_id),
                wid,
            )
            return JsonResponse({"detail": "lease_owner_mismatch_or_expired"}, status=409)

        logger.info(
            "heartbeat ok video_id=%s worker=%s",
            str(video_id),
            wid,
        )
        return JsonResponse({"ok": True}, status=200)
