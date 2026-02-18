# apps/support/video/views/progress_views.py

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


class VideoProgressView(APIView):
    """비디오 진행률/상태 조회 (Redis-only, DB 부하 0)"""
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request, pk):
        """GET /media/videos/{id}/progress/"""
        video_id = int(pk)
        tenant = getattr(request, "tenant", None)
        
        if not tenant:
            return Response(
                {"detail": "tenant가 필요합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        # ✅ Redis에서 상태 조회 (Tenant 네임스페이스)
        cached_status = get_video_status_from_redis(tenant.id, video_id)
        
        if not cached_status:
            # ✅ Redis에 없으면 UNKNOWN 상태 반환 (404는 UX상 위험)
            # TTL 만료되었지만 아직 READY 상태일 수 있음
            return Response(
                {"status": "UNKNOWN", "message": "진행 상태를 확인할 수 없습니다."},
                status=status.HTTP_200_OK,
            )
        
        video_status = cached_status.get("status")
        
        # ✅ 진행률은 Redis에서 조회 (tenant_id 전달 필수)
        progress = None
        step_detail = None
        remaining_seconds = None
        
        if video_status == "PROCESSING":
            # ✅ tenant_id 전달 필수
            progress = get_video_encoding_progress(video_id, tenant.id)
            step_detail = get_video_encoding_step_detail(video_id, tenant.id)
            remaining_seconds = get_video_encoding_remaining_seconds(video_id, tenant.id)
        
        # ✅ 응답 구성
        response_data = {
            "id": video_id,
            "status": video_status,
            "encoding_progress": progress,
            "encoding_remaining_seconds": remaining_seconds,
            "encoding_step_index": step_detail.get("step_index") if step_detail else None,
            "encoding_step_total": step_detail.get("step_total") if step_detail else None,
            "encoding_step_name": step_detail.get("step_name_display") if step_detail else None,
            "encoding_step_percent": step_detail.get("step_percent") if step_detail else None,
        }
        
        # ✅ 완료 상태면 추가 정보 포함
        if video_status in ["READY", "FAILED"]:
            response_data["hls_path"] = cached_status.get("hls_path")
            response_data["duration"] = cached_status.get("duration")
            if video_status == "FAILED":
                response_data["error_reason"] = cached_status.get("error_reason")
        
        return Response(response_data)


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
