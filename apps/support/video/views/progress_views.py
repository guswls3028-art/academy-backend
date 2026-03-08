# apps/support/video/views/progress_views.py

# Progress endpoint: Redis-first. On Redis miss return {"state": "UNKNOWN"}.
# Tenant 격리: request.tenant 소속 Video만 조회. 다른 테넌트 진행률 노출 금지.

from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from django_filters.rest_framework import DjangoFilterBackend

from django.utils import timezone
from ..models import VideoProgress, VideoAccess, AccessMode
from ..serializers import VideoProgressSerializer
from academy.adapters.db.django import repositories_video as video_repo
from apps.support.video.encoding_progress import (
    get_video_encoding_progress,
    get_video_encoding_step_detail,
    get_video_encoding_remaining_seconds,
)
from apps.support.video.redis_status_cache import (
    get_video_status_from_redis,
)
from apps.support.video.services.ops_events import emit_progress_layer_metrics


def _default_progress_response(video_id: int):
    """Redis에 진행 정보 없을 때 반환. DB 접근 금지."""
    resp = Response({
        "id": video_id,
        "status": "PENDING",
        "progress": 0,
        "encoding_progress": 0,
        "encoding_remaining_seconds": None,
        "encoding_step_index": None,
        "encoding_step_total": None,
        "encoding_step_name": None,
        "encoding_step_percent": None,
    }, status=status.HTTP_200_OK)
    resp["Retry-After"] = "3"
    return resp


def _unknown_state_response(video_id: int):
    """Redis status 키 없음 — DB 조회 없이 반환 (PROGRESS ENDPOINT)."""
    resp = Response({
        "id": video_id,
        "state": "UNKNOWN",
        "status": "UNKNOWN",
        "progress": 0,
        "encoding_progress": 0,
        "encoding_remaining_seconds": None,
        "encoding_step_index": None,
        "encoding_step_total": None,
        "encoding_step_name": None,
        "encoding_step_percent": None,
    }, status=status.HTTP_200_OK)
    resp["Retry-After"] = "3"
    return resp


class VideoProgressView(APIView):
    """비디오 진행률/상태 조회 (Redis-only). DB 부하 0. Redis miss 시 state=UNKNOWN 반환."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        # DO NOT ADD DB ACCESS HERE (PROGRESS ENDPOINT)
        try:
            video_id = int(pk)
        except (TypeError, ValueError):
            return Response(
                {"detail": "Invalid video id."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response(
                {"detail": "tenant가 필요합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            cached_status = get_video_status_from_redis(tenant.id, video_id)
        except Exception:
            emit_progress_layer_metrics(progress_requests=1, redis_miss=0, db_hit=0)
            return _default_progress_response(video_id)

        if cached_status is None:
            # request.tenant 소속 Video만 조회. 다른 테넌트 영상 진행률 노출 금지(tenant 격리).
            video = None
            try:
                from ..models import Video

                video = (
                    Video.objects.filter(
                        session__lecture__tenant=tenant,
                        pk=video_id,
                    )
                    .select_related("session__lecture__tenant")
                    .first()
                )
                if video:
                    # 동일 테넌트 내에서만 Redis/DB fallback 허용
                    cached_status = get_video_status_from_redis(tenant.id, video_id)
            except Exception:
                pass

        if cached_status is None and video is not None:
            # Fallback 2: Redis 미사용 시(워커 Redis 미연결 등) DB에서 RUNNING/READY 여부 확인
            try:
                from ..models import VideoTranscodeJob

                running = VideoTranscodeJob.objects.filter(
                    video_id=video_id,
                    state=VideoTranscodeJob.State.RUNNING,
                ).exists()
                if running:
                    cached_status = {"status": "PROCESSING"}
                    if video.session and video.session.lecture:
                        tenant = video.session.lecture.tenant
                elif str(video.status) == "READY" and video.hls_path:
                    cached_status = {
                        "status": "READY",
                        "hls_path": video.hls_path,
                        "duration": video.duration,
                    }
                    if video.session and video.session.lecture:
                        tenant = video.session.lecture.tenant
                elif str(video.status) == "FAILED":
                    cached_status = {
                        "status": "FAILED",
                        "error_reason": getattr(video, "error_reason", "") or "",
                    }
                    if video.session and video.session.lecture:
                        tenant = video.session.lecture.tenant
            except Exception:
                pass

        if cached_status is None:
            emit_progress_layer_metrics(progress_requests=1, redis_miss=1, db_hit=0)
            return _unknown_state_response(video_id)

        try:
            video_status = cached_status.get("status") if isinstance(cached_status, dict) else "PENDING"
            video_status = video_status or "PENDING"
            progress = None
            step_detail = None
            remaining_seconds = None

            if video_status == "PROCESSING":
                try:
                    progress = get_video_encoding_progress(video_id, tenant.id)
                    step_detail = get_video_encoding_step_detail(video_id, tenant.id)
                    remaining_seconds = get_video_encoding_remaining_seconds(video_id, tenant.id)
                except Exception:
                    progress = 0

            encoding_pct = progress if progress is not None else 0
            response_data = {
                "id": video_id,
                "status": video_status,
                "progress": encoding_pct,
                "encoding_progress": encoding_pct,
                "encoding_remaining_seconds": remaining_seconds,
                "encoding_step_index": step_detail.get("step_index") if step_detail else None,
                "encoding_step_total": step_detail.get("step_total") if step_detail else None,
                "encoding_step_name": step_detail.get("step_name_display") if step_detail else None,
                "encoding_step_percent": step_detail.get("step_percent") if step_detail else None,
            }
            if video_status in ["READY", "FAILED"] and isinstance(cached_status, dict):
                response_data["hls_path"] = cached_status.get("hls_path")
                response_data["duration"] = cached_status.get("duration")
                if video_status == "FAILED":
                    response_data["error_reason"] = cached_status.get("error_reason")

            emit_progress_layer_metrics(progress_requests=1, redis_miss=0, db_hit=0)
            resp = Response(response_data)
            resp["Retry-After"] = "3"
            return resp
        except Exception:
            emit_progress_layer_metrics(progress_requests=1, redis_miss=0, db_hit=0)
            return _default_progress_response(video_id)


class VideoProgressViewSet(ModelViewSet):
    queryset = video_repo.video_progress_all()
    serializer_class = VideoProgressSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["video", "enrollment"]
    permission_classes = [IsAuthenticated]

    def perform_update(self, serializer):
        vp = serializer.instance
        prev_completed = vp.completed

        vp = serializer.save()

        # PROCTORED_CLASS → FREE_REVIEW on completion (SSOT)
        if not prev_completed and vp.completed:
            now = timezone.now()
            video_repo.video_access_filter(vp.video, vp.enrollment).filter(
                access_mode=AccessMode.PROCTORED_CLASS,
            ).update(
                access_mode=AccessMode.FREE_REVIEW,
                proctored_completed_at=now,
                is_override=False,
            )
            video_repo.video_access_filter(vp.video, vp.enrollment).filter(
                rule="once",
            ).update(rule="free", is_override=False)
