# PATH: apps/support/video/views/internal_video_worker.py

from __future__ import annotations

from typing import Any, Optional

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone

from rest_framework.views import APIView
from rest_framework.permissions import AllowAny

from apps.support.video.models import Video
from apps.support.video.services.queue import VideoJobQueue


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


def _parse_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


LEASE_SECONDS = int(getattr(settings, "VIDEO_WORKER_LEASE_SECONDS", 60))
MAX_BATCH = int(getattr(settings, "VIDEO_WORKER_MAX_BATCH", 1))


class VideoWorkerClaimNextView(APIView):
    """
    Worker polls:
      GET /internal/video-worker/next/

    Returns:
      200 { "job": { video_id, file_key } }
      204 no content

    ✅ SSOT:
    - claim은 VideoJobQueue.claim_next
    - 상태 변화는 Video.Status 기준
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        if not _require_worker_token(request):
            expected = str(getattr(settings, "INTERNAL_WORKER_TOKEN", "") or "")
            if not expected:
                return JsonResponse(
                    {"detail": "INTERNAL_WORKER_TOKEN not configured"},
                    status=503,
                )
            return JsonResponse({"detail": "Unauthorized"}, status=401)

        wid = _worker_id(request)

        video = VideoJobQueue.claim_next(
            worker_id=wid,
            lease_seconds=LEASE_SECONDS,
            max_batch=MAX_BATCH,
        )

        if video is None:
            return JsonResponse({}, status=204)

        return JsonResponse(
            {"job": {"video_id": int(video.id), "file_key": str(video.file_key or "")}},
            status=200,
        )


class VideoWorkerCompleteView(APIView):
    """
    Worker reports success:
      POST /internal/video-worker/{video_id}/complete/
      body: { hls_path, duration }

    ✅ SSOT:
    - owner(leased_by)만 complete 가능
    - 멱등 처리: 이미 READY + hls_path면 OK
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

        data = getattr(request, "data", None) or {}
        hls_path = data.get("hls_path")
        duration = _parse_int(data.get("duration"))

        if not hls_path:
            return JsonResponse({"detail": "hls_path required"}, status=400)

        ok, reason = VideoJobQueue.complete(
            video_id=int(video_id),
            worker_id=wid,
            hls_path=str(hls_path),
            duration=duration,
        )

        if not ok:
            if reason == "not_found":
                return JsonResponse({"detail": "Not found"}, status=404)
            if reason == "lease_owner_mismatch":
                return JsonResponse({"detail": "lease_owner_mismatch"}, status=409)
            return JsonResponse({"detail": reason}, status=400)

        return JsonResponse({"ok": True, "reason": reason}, status=200)


class VideoWorkerFailView(APIView):
    """
    Worker reports failure:
      POST /internal/video-worker/{video_id}/fail/
      body: { reason }

    ✅ SSOT:
    - owner(leased_by)만 fail 가능
    - 멱등 처리: 이미 FAILED면 OK
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

        data = getattr(request, "data", None) or {}
        reason = str(data.get("reason") or "unknown")

        ok, why = VideoJobQueue.fail(
            video_id=int(video_id),
            worker_id=wid,
            reason=reason,
        )

        if not ok:
            if why == "not_found":
                return JsonResponse({"detail": "Not found"}, status=404)
            if why == "lease_owner_mismatch":
                return JsonResponse({"detail": "lease_owner_mismatch"}, status=409)
            return JsonResponse({"detail": why}, status=400)

        return JsonResponse({"ok": True, "reason": why}, status=200)
