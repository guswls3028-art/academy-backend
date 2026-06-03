# apps/support/video/views/progress_views.py

# Progress endpoint: Redis-first. On Redis miss return {"state": "UNKNOWN"}.
# Tenant 격리: request.tenant 소속 Video만 조회. 다른 테넌트 진행률 노출 금지.

from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from django_filters.rest_framework import DjangoFilterBackend

from apps.core.permissions import (
    TenantResolvedAndMember,
    TenantResolvedAndStaff as _TenantResolvedAndStaff,
)

from django.utils import timezone
from ..models import VideoAccess, AccessMode
from ..serializers import VideoProgressSerializer
from academy.adapters.db.django import repositories_video as video_repo
from apps.domains.video.encoding_progress import (
    get_video_encoding_snapshot,
)
from apps.domains.video.redis_status_cache import (
    get_video_status_from_redis,
)
from apps.domains.video.policy import crossed_video_completion_threshold
from apps.domains.video.services.ops_events import emit_progress_layer_metrics


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

    permission_classes = [IsAuthenticated, TenantResolvedAndMember]

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
                    snapshot = get_video_encoding_snapshot(video_id, tenant.id)
                    progress = snapshot.get("progress")
                    step_detail = snapshot.get("step_detail")
                    remaining_seconds = snapshot.get("remaining_seconds")
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
    serializer_class = VideoProgressSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["video", "enrollment"]
    permission_classes = [IsAuthenticated, _TenantResolvedAndStaff]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return video_repo.video_progress_all().none()
        return video_repo.video_progress_all().filter(
            video__tenant=tenant,
        )

    def perform_update(self, serializer):
        vp = serializer.instance
        prev_completed = vp.completed
        prev_progress = vp.progress

        vp = serializer.save()

        # PROCTORED_CLASS → FREE_REVIEW SSOT.
        # Trigger: completed=True OR progress crosses the domain completion threshold.
        # progress가 Redis-DB lag로 흔들릴 때 proctored_completed_at을 박아두면
        # 다음 resolve가 안정적으로 FREE_REVIEW를 반환.
        cur_progress = vp.progress
        crossed_threshold = crossed_video_completion_threshold(prev_progress, cur_progress)
        just_completed = (not prev_completed) and vp.completed

        if just_completed or crossed_threshold:
            now = timezone.now()
            existing = video_repo.video_access_filter(vp.video, vp.enrollment)
            if existing.exists():
                existing.filter(access_mode=AccessMode.PROCTORED_CLASS).update(
                    access_mode=AccessMode.FREE_REVIEW,
                    proctored_completed_at=now,
                    is_override=False,
                )
                existing.filter(rule="once").update(rule="free", is_override=False)
                # PROCTORED 외 모드여도 시간만 기록 (감사 추적)
                existing.filter(proctored_completed_at__isnull=True).update(
                    proctored_completed_at=now,
                )
            else:
                # access 레코드가 없는 학생 — 명시적으로 완료 시간 기록
                # (access_resolver는 perm 없으면 attendance 기반 평가 → 다음 resolve에서 FREE_REVIEW)
                VideoAccess.objects.create(
                    video=vp.video,
                    enrollment=vp.enrollment,
                    access_mode=AccessMode.FREE_REVIEW,
                    proctored_completed_at=now,
                    is_override=False,
                )
