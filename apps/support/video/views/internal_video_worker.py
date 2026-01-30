# PATH: apps/support/video/views/internal_video_worker.py

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.db import models  # ✅ 원본 의미 유지 + 동작 보강 (기존 파일 하단 import 대체)

from rest_framework.views import APIView
from rest_framework.permissions import AllowAny

from apps.support.video.models import Video


def _require_worker_token(request) -> bool:
    token = request.headers.get("X-Worker-Token")
    return bool(token) and token == getattr(settings, "INTERNAL_WORKER_TOKEN", "")


def _worker_id(request) -> str:
    return (
        request.headers.get("X-Worker-Id")
        or request.headers.get("X-Worker-ID")
        or "worker-unknown"
    )


LEASE_SECONDS = int(getattr(settings, "VIDEO_WORKER_LEASE_SECONDS", 60))
MAX_BATCH = int(getattr(settings, "VIDEO_WORKER_MAX_BATCH", 1))


def _lease_cutoff(now):
    # DB 스키마 변경 없이 "PROCESSING stuck" 회수:
    # - processing 상태가 LEASE_SECONDS 이상 갱신이 없으면 reclaim 허용
    return now - timedelta(seconds=LEASE_SECONDS)


def _parse_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _is_lease_owner(video: Video, worker_id: str) -> bool:
    """
    ✅ 다중 노드 중복 처리 방지:
    - leased_by가 설정돼 있으면 소유자만 complete/fail/heartbeat 가능
    - leased_until이 지나면(만료) owner 체크를 완화(회수/재처리 허용)
    - 기존 구조(updated_at reclaim) 그대로 유지하면서 중앙 통제 추가
    """
    if not hasattr(video, "leased_by") or not hasattr(video, "leased_until"):
        return True

    lb = (getattr(video, "leased_by", "") or "").strip()
    lu = getattr(video, "leased_until", None)

    if not lb:
        return True

    # lease 만료면 소유권 강제 안 함(회수 가능)
    if lu is not None and lu <= timezone.now():
        return True

    return lb == (worker_id or "")


class VideoWorkerClaimNextView(APIView):
    """
    Worker polls:
      GET /api/v1/internal/video-worker/next/
    Returns:
      200 { "job": {video_id, file_key} }  OR  204 no content

    NOTE:
    - 기존 reclaim 로직(updated_at 기준) 유지
    - 여기에 leased_by/leased_until만 "추가 기록"하여
      multi-host 환경에서 완료처리 경합 방지
    """
    permission_classes = [AllowAny]

    def get(self, request):
        if not _require_worker_token(request):
            return JsonResponse({"detail": "Unauthorized"}, status=401)

        now = timezone.now()
        cutoff = _lease_cutoff(now)

        wid = _worker_id(request)

        with transaction.atomic():
            video = (
                Video.objects.select_for_update(skip_locked=True)
                .filter(
                    models.Q(status=Video.Status.UPLOADED)
                    | models.Q(status=Video.Status.PROCESSING, updated_at__lt=cutoff)
                )
                .order_by("id")
                .first()
            )

            if video is None:
                return JsonResponse({}, status=204)

            # reclaim된 작업은 실패사유 남기지 않고 재처리
            video.status = Video.Status.PROCESSING

            # ✅ lease 필드가 있으면 반드시 기록(중복 완료 방지)
            if hasattr(video, "processing_started_at"):
                video.processing_started_at = now
            if hasattr(video, "leased_until"):
                video.leased_until = now + timedelta(seconds=LEASE_SECONDS)
            if hasattr(video, "leased_by"):
                video.leased_by = wid

            update_fields = ["status"]
            if hasattr(video, "processing_started_at"):
                update_fields.append("processing_started_at")
            if hasattr(video, "leased_until"):
                update_fields.append("leased_until")
            if hasattr(video, "leased_by"):
                update_fields.append("leased_by")

            video.save(update_fields=update_fields)

        return JsonResponse(
            {"job": {"video_id": video.id, "file_key": video.file_key}},
            status=200,
        )


class VideoWorkerCompleteView(APIView):
    """
    Worker reports success:
      POST /api/v1/internal/video-worker/{video_id}/complete/
      body: {hls_path, duration}

    idempotent:
    - 이미 READY면 ok 반환

    ✅ 보강:
    - lease owner(worker_id)만 complete 가능
      (multi-host 이중 처리의 "완료 경합" 차단)
    """
    permission_classes = [AllowAny]

    def post(self, request, video_id: int):
        if not _require_worker_token(request):
            return JsonResponse({"detail": "Unauthorized"}, status=401)

        wid = _worker_id(request)

        data = request.data if hasattr(request, "data") else {}
        hls_path = data.get("hls_path")
        duration = _parse_int(data.get("duration"))

        if not hls_path:
            return JsonResponse({"detail": "hls_path required"}, status=400)

        with transaction.atomic():
            video = Video.objects.select_for_update().filter(id=video_id).first()
            if video is None:
                return JsonResponse({"detail": "Not found"}, status=404)

            # ✅ lease owner 검증
            if not _is_lease_owner(video, wid):
                return JsonResponse({"detail": "lease_owner_mismatch"}, status=409)

            # ✅ idempotent 처리
            if video.status == Video.Status.READY and video.hls_path:
                return JsonResponse({"ok": True, "idempotent": True}, status=200)

            video.hls_path = str(hls_path)
            if duration is not None and duration >= 0:
                video.duration = duration

            video.status = Video.Status.READY

            # lease 해제
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

        return JsonResponse({"ok": True}, status=200)


class VideoWorkerFailView(APIView):
    """
    Worker reports failure:
      POST /api/v1/internal/video-worker/{video_id}/fail/
      body: {reason}

    idempotent:
    - 이미 FAILED면 ok 반환

    ✅ 보강:
    - lease owner(worker_id)만 fail 가능
    """
    permission_classes = [AllowAny]

    def post(self, request, video_id: int):
        if not _require_worker_token(request):
            return JsonResponse({"detail": "Unauthorized"}, status=401)

        wid = _worker_id(request)

        data = request.data if hasattr(request, "data") else {}
        reason = data.get("reason") or "unknown"

        with transaction.atomic():
            video = Video.objects.select_for_update().filter(id=video_id).first()
            if video is None:
                return JsonResponse({"detail": "Not found"}, status=404)

            # ✅ lease owner 검증
            if not _is_lease_owner(video, wid):
                return JsonResponse({"detail": "lease_owner_mismatch"}, status=409)

            # ✅ idempotent 처리
            if video.status == Video.Status.FAILED:
                return JsonResponse({"ok": True, "idempotent": True}, status=200)

            video.status = Video.Status.FAILED

            if hasattr(video, "error_reason"):
                video.error_reason = str(reason)[:2000]

            # lease 해제
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

        return JsonResponse({"ok": True}, status=200)
