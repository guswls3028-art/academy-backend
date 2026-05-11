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


def _humanize_error_reason(raw: str) -> str:
    """백엔드 error_reason → 비기술 사용자용 한국어 사유."""
    if not raw:
        return ""
    s = str(raw).lower()
    if "source_not_found" in s or "s3 object not found" in s:
        return "원본 파일을 찾을 수 없습니다 (저장소에서 사라짐)"
    if "ffprobe_failed" in s or "no_video_stream" in s or "duration_invalid" in s:
        return "영상 형식을 인식할 수 없습니다 (손상된 파일일 수 있음)"
    if "duration_missing" in s:
        return "영상 길이를 읽을 수 없습니다"
    if "presigned_get_failed" in s:
        return "스토리지 접근 실패 (잠시 후 다시 시도)"
    if "tenant_limit" in s:
        return "처리 대기열이 가득 찼습니다"
    if "global_limit" in s:
        return "전체 처리 대기열이 가득 찼습니다"
    if "stale_running" in s:
        return "이전 처리가 멈춰 자동 정리됨"
    return raw[:80]  # 알 수 없는 사유는 원문 일부만 노출


def _serialize_video_summary(v: Video) -> dict:
    session = getattr(v, "session", None)
    lecture = getattr(session, "lecture", None) if session else None
    raw_reason = getattr(v, "error_reason", "") or ""
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
        "error_reason": _humanize_error_reason(raw_reason),
        "error_reason_raw": raw_reason,
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
