# PATH: apps/support/video/views/internal_views.py

from __future__ import annotations

from typing import Optional
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework import status

from apps.support.video.services.queue import VideoJobQueue


# =========================
# 🔒 SAME AS AI WORKER
# =========================
def _require_worker_auth(request) -> Optional[Response]:
    expected = str(getattr(settings, "INTERNAL_WORKER_TOKEN", "") or "")
    if not expected:
        return Response(
            {"detail": "INTERNAL_WORKER_TOKEN not configured"},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    token = (
        request.headers.get("X-Worker-Token")
        or request.META.get("HTTP_X_WORKER_TOKEN")
        or ""
    )

    if str(token) != expected:
        return Response(
            {"detail": "Unauthorized worker"},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    return None


def _worker_id(request) -> str:
    return (
        request.headers.get("X-Worker-Id")
        or request.META.get("HTTP_X_WORKER_ID")
        or "video-worker"
    )


# =========================
# 🎬 NEXT JOB
# =========================
class InternalVideoJobNextView(APIView):
    """
    GET /internal/video-worker/next/
    response:
      { "job": {...} } | { "job": null }
    """

    permission_classes = [AllowAny]

    def get(self, request):
        auth = _require_worker_auth(request)
        if auth:
            return auth

        q = VideoJobQueue()
        job = q.claim(worker_id=_worker_id(request))

        if not job:
            return Response({"job": None}, status=status.HTTP_200_OK)

        return Response(
            {
                "job": {
                    "id": job.id,
                    "video_id": job.video_id,
                    "input_key": job.input_key,
                    "output_prefix": job.output_prefix,
                }
            },
            status=status.HTTP_200_OK,
        )
