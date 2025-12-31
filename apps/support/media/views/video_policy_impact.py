# apps/support/media/views/video_policy_impact.py

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from django.shortcuts import get_object_or_404

from apps.support.media.models import Video
from apps.support.media.services.video_stats import build_video_stats_students


def parse_bool(v, default):
    if v is None:
        return default
    return str(v).lower() in ("1", "true", "yes", "on")


def parse_float(v, default):
    try:
        return float(v)
    except Exception:
        return default


class VideoPolicyImpactAPIView(APIView):
    """
    ✅ 저장 전 정책 변경 영향 미리보기
    - DB 변경 ❌
    - stats()와 100% 동일한 학생/룰 계산 사용
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, video_id: int):
        video = get_object_or_404(Video, pk=video_id)

        proposed_allow_skip = parse_bool(
            request.query_params.get("allow_skip"),
            video.allow_skip,
        )
        proposed_max_speed = parse_float(
            request.query_params.get("max_speed"),
            float(video.max_speed or 1.0),
        )
        proposed_show_watermark = parse_bool(
            request.query_params.get("show_watermark"),
            video.show_watermark,
        )

        students = build_video_stats_students(video)

        breakdown = {"free": 0, "once": 0, "blocked": 0}
        for s in students:
            r = s["effective_rule"]
            breakdown[r] = breakdown.get(r, 0) + 1

        changed = (
            video.allow_skip != proposed_allow_skip
            or float(video.max_speed or 1.0) != proposed_max_speed
            or video.show_watermark != proposed_show_watermark
        )

        eligible_count = breakdown["free"] + breakdown["once"]
        impacted_count = eligible_count if changed else 0

        return Response({
            "eligible_count": eligible_count,
            "impacted_count": impacted_count,
            "changed_fields": {
                "allow_skip": {
                    "before": video.allow_skip,
                    "after": proposed_allow_skip,
                },
                "max_speed": {
                    "before": float(video.max_speed or 1.0),
                    "after": proposed_max_speed,
                },
                "show_watermark": {
                    "before": video.show_watermark,
                    "after": proposed_show_watermark,
                },
            },
            "breakdown_by_rule": breakdown,
            "sample": [
                {
                    "enrollment": s["enrollment"],
                    "student_name": s["student_name"],
                    "effective_rule": s["effective_rule"],
                }
                for s in students[:10]
            ],
        })
