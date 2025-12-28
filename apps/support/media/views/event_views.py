# apps/support/media/views/event_views.py

from collections import defaultdict
from django.db.models import Count, Q
from rest_framework.viewsets import ReadOnlyModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination

from ..models import VideoPlaybackEvent
from ..serializers import VideoPlaybackEventListSerializer


# =========================
# Pagination
# =========================
class StandardPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200


# =========================
# Severity / Risk Scoring
# =========================
EVENT_WEIGHTS = {
    # 핵심 정책 위반류
    "SEEK_ATTEMPT": 5,
    "SPEED_CHANGE_ATTEMPT": 4,

    # 의심 행동
    "VISIBILITY_HIDDEN": 2,
    "FOCUS_LOST": 2,

    # 환경/오류(패널티 낮게)
    "PLAYER_ERROR": 1,

    # 나머지 기본값
}

VIOLATED_BONUS = 6  # violated=True면 가산점


def _event_weight(event_type: str) -> int:
    return int(EVENT_WEIGHTS.get(event_type, 1))


def _risk_level(score: int) -> str:
    # 운영하면서 튜닝하면 됨
    if score >= 35:
        return "high"
    if score >= 18:
        return "mid"
    return "low"


# =========================
# ViewSet
# =========================
class VideoPlaybackEventViewSet(ReadOnlyModelViewSet):
    """
    운영자용 이벤트 로그 조회 API
    GET /media/video-events/?video=1&violated=1&event_type=SEEK_ATTEMPT&search=홍&page=1&page_size=50
    """
    queryset = (
        VideoPlaybackEvent.objects
        .all()
        .select_related("enrollment", "enrollment__student", "video")
        .order_by("-received_at", "-id")
    )
    serializer_class = VideoPlaybackEventListSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination

    def list(self, request, *args, **kwargs):
        qp = request.query_params

        video_id = qp.get("video")
        if not video_id:
            return Response({"detail": "video is required"}, status=400)

        violated = qp.get("violated")  # "1" or "0"
        event_type = qp.get("event_type")
        enrollment_id = qp.get("enrollment")
        user_id = qp.get("user_id")
        search = (qp.get("search") or "").strip()

        base = self.get_queryset().filter(video_id=video_id)

        if violated in ("0", "1"):
            base = base.filter(violated=(violated == "1"))

        if event_type:
            base = base.filter(event_type=event_type)

        if enrollment_id:
            base = base.filter(enrollment_id=enrollment_id)

        if user_id:
            base = base.filter(user_id=user_id)

        if search:
            base = base.filter(
                Q(enrollment__student__name__icontains=search)
                | Q(session_id__icontains=search)
                | Q(violation_reason__icontains=search)
            )

        # ---------- summary ----------
        # video 단위 요약 (현재 필터와 무관하게 전체 기반으로 주고 싶으면 base 대신 total_qs 사용)
        total_qs = self.get_queryset().filter(video_id=video_id)

        summary = {
            "total_events": int(total_qs.count()),
            "violations": int(total_qs.filter(violated=True).count()),
            "seek_attempts": int(total_qs.filter(event_type="SEEK_ATTEMPT").count()),
            "speed_attempts": int(total_qs.filter(event_type="SPEED_CHANGE_ATTEMPT").count()),
            "visibility_hidden": int(total_qs.filter(event_type="VISIBILITY_HIDDEN").count()),
            "focus_lost": int(total_qs.filter(event_type="FOCUS_LOST").count()),
        }

        # ---------- risk students ----------
        # video 전체 이벤트를 학생별로 점수화 (top 10)
        score_map = defaultdict(lambda: {"score": 0, "violations": 0, "counts": defaultdict(int), "student_name": "-", "enrollment": None})

        for e in total_qs.only(
            "enrollment_id", "event_type", "violated",
            "enrollment__student__name"
        ):
            sid = int(e.enrollment_id)
            score_map[sid]["enrollment"] = sid
            score_map[sid]["student_name"] = getattr(getattr(e.enrollment, "student", None), "name", "-") if hasattr(e, "enrollment") else score_map[sid]["student_name"]

            score_map[sid]["counts"][e.event_type] += 1

            w = _event_weight(e.event_type)
            if e.violated:
                w += VIOLATED_BONUS
                score_map[sid]["violations"] += 1

            score_map[sid]["score"] += w

        risk_students = []
        for sid, v in score_map.items():
            score = int(v["score"])
            risk_students.append({
                "enrollment": sid,
                "student_name": v["student_name"],
                "score": score,
                "level": _risk_level(score),
                "violations": int(v["violations"]),
                "top_events": sorted(
                    [{"type": k, "count": int(c)} for k, c in v["counts"].items()],
                    key=lambda x: x["count"],
                    reverse=True,
                )[:3],
            })

        risk_students.sort(key=lambda x: x["score"], reverse=True)
        risk_students = risk_students[:10]

        # ---------- paginated events (filtered base) ----------
        page = self.paginate_queryset(base)
        ser = self.get_serializer(page, many=True)
        paginated = self.get_paginated_response(ser.data).data

        return Response({
            "summary": summary,
            "risk_students": risk_students,
            "events": paginated,
        })
