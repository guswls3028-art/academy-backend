# PATH: apps/support/video/views/admin_landing_stats_view.py
"""
Admin Videos Landing Stats

GET /media/admin/videos/landing-stats/

영상 도메인 첫 화면(KPI 인박스) 전용 집계 엔드포인트.
- 테넌트 격리 절대 (Video.tenant 직접 필터).
- soft-delete 제외 (default Manager).
"""
from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.video.models import Video


PROCESSING_STATUSES = [
    Video.Status.PENDING,
    Video.Status.UPLOADED,
    Video.Status.PROCESSING,
]


def _serialize_video_summary(v: Video) -> dict:
    session = getattr(v, "session", None)
    lecture = getattr(session, "lecture", None) if session else None
    return {
        "id": int(v.id),
        "title": v.title or "",
        "status": v.status,
        "session_id": int(session.id) if session else None,
        "lecture_id": int(lecture.id) if lecture else None,
        "lecture_title": (lecture.title if lecture else "") or "",
        "session_order": int(session.order) if session and getattr(session, "order", None) else None,
        "created_at": v.created_at.isoformat() if v.created_at else None,
        "view_count": int(v.view_count) if v.view_count is not None else 0,
    }


class AdminVideosLandingStatsView(APIView):
    """
    GET /media/admin/videos/landing-stats/

    Response:
    {
      "total": int,                # 등록된 영상 (soft-delete 제외)
      "ready": int,                # READY 상태
      "processing": int,           # PENDING/UPLOADED/PROCESSING 합산
      "failed": int,               # FAILED
      "uploaded_last_7d": int,
      "processing_top": [VideoSummary, ...],   # 최근 처리 중 5건
      "failed_top": [VideoSummary, ...]
    }
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response(
                {
                    "total": 0,
                    "ready": 0,
                    "processing": 0,
                    "failed": 0,
                    "uploaded_last_7d": 0,
                    "processing_top": [],
                    "failed_top": [],
                },
                status=200,
            )

        now = timezone.now()
        cutoff_7d = now - timedelta(days=7)

        qs = Video.objects.filter(tenant=tenant)
        total = qs.count()
        ready = qs.filter(status=Video.Status.READY).count()
        processing = qs.filter(status__in=PROCESSING_STATUSES).count()
        failed = qs.filter(status=Video.Status.FAILED).count()
        uploaded_7d = qs.filter(created_at__gte=cutoff_7d).count()

        processing_top = list(
            qs.filter(status__in=PROCESSING_STATUSES)
            .select_related("session__lecture")
            .order_by("-created_at")[:5]
        )
        failed_top = list(
            qs.filter(status=Video.Status.FAILED)
            .select_related("session__lecture")
            .order_by("-created_at")[:5]
        )

        return Response(
            {
                "total": int(total),
                "ready": int(ready),
                "processing": int(processing),
                "failed": int(failed),
                "uploaded_last_7d": int(uploaded_7d),
                "processing_top": [_serialize_video_summary(v) for v in processing_top],
                "failed_top": [_serialize_video_summary(v) for v in failed_top],
            },
            status=200,
        )
