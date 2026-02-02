# PATH: apps/support/video/views/internal_views.py

from __future__ import annotations

from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status

from apps.support.video.models import Video


class VideoProcessingCompleteView(APIView):
    """
    ✅ Legacy ACK endpoint (kept)

    기존 계약을 깨지 않기 위해 유지하되,
    "worker queue/claim" 같은 책임을 절대 섞지 않는다.

    POST /api/v1/videos/internal/videos/<video_id>/processing-complete/
    (프로젝트의 기존 URL 연결 방식에 맞춰 유지)

    body:
      {
        "hls_path": "...",
        "duration": 123
      }
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, video_id: int):
        data = getattr(request, "data", None) or {}

        hls_path = data.get("hls_path")
        if not hls_path:
            return Response({"detail": "hls_path required"}, status=status.HTTP_400_BAD_REQUEST)

        duration = data.get("duration")
        try:
            duration_int = int(duration) if duration is not None else None
        except Exception:
            duration_int = None

        video = Video.objects.filter(id=int(video_id)).first()
        if not video:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        # 멱등
        if video.status == Video.Status.READY and bool(video.hls_path):
            return Response({"ok": True, "idempotent": True}, status=status.HTTP_200_OK)

        video.hls_path = str(hls_path)
        if duration_int is not None and duration_int >= 0:
            video.duration = duration_int
        video.status = Video.Status.READY

        # legacy complete는 lease 통제를 모를 수 있으므로 안전하게 lease 해제만 수행
        if hasattr(video, "leased_until"):
            video.leased_until = None
        if hasattr(video, "leased_by"):
            video.leased_by = ""

        update_fields = ["hls_path", "status"]
        if duration_int is not None and duration_int >= 0:
            update_fields.append("duration")
        if hasattr(video, "leased_until"):
            update_fields.append("leased_until")
        if hasattr(video, "leased_by"):
            update_fields.append("leased_by")

        video.save(update_fields=update_fields)

        return Response({"ok": True}, status=status.HTTP_200_OK)
