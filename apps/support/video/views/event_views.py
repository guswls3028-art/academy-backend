# PATH: apps/support/video/views/event_views.py

import csv
from datetime import timedelta

from django.http import HttpResponse
from django.utils import timezone

from rest_framework.viewsets import ReadOnlyModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.filters import SearchFilter

from django_filters.rest_framework import DjangoFilterBackend

from ..models import VideoPlaybackEvent
from ..serializers import (
    VideoPlaybackEventListSerializer,
    VideoRiskRowSerializer,
)


def _range_to_since(range_key: str):
    now = timezone.now()
    if range_key == "24h":
        return now - timedelta(hours=24)
    if range_key == "7d":
        return now - timedelta(days=7)
    return None


def _event_score(event_type: str, violated: bool, violation_reason: str | None):
    weights = {
        "VISIBILITY_HIDDEN": 1,
        "VISIBILITY_VISIBLE": 0,
        "FOCUS_LOST": 2,
        "FOCUS_GAINED": 0,
        "SEEK_ATTEMPT": 3,
        "SPEED_CHANGE_ATTEMPT": 3,
        "FULLSCREEN_ENTER": 0,
        "FULLSCREEN_EXIT": 0,
        "PLAYER_ERROR": 1,
    }
    w = int(weights.get(event_type, 1))
    if violated:
        w *= 2
    if violation_reason:
        w += 1
    return w


class VideoPlaybackEventViewSet(ReadOnlyModelViewSet):
    """
    Admin / Staff 전용
    - list
    - risk
    - export
    """

    queryset = (
        VideoPlaybackEvent.objects
        .all()
        .select_related("enrollment", "enrollment__student", "video")
    )
    serializer_class = VideoPlaybackEventListSerializer
    permission_classes = [IsAuthenticated]

    # ✅ 검색 + 필터 동시 지원
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["video", "enrollment", "violated"]
    search_fields = [
        "enrollment__student__name",
        "session_id",
        "user_id",
    ]

    def get_queryset(self):
        qs = super().get_queryset()

        video_id = self.request.query_params.get("video")
        if video_id:
            qs = qs.filter(video_id=video_id)

        range_key = self.request.query_params.get("range", "24h")
        since = _range_to_since(range_key)
        if since:
            qs = qs.filter(occurred_at__gte=since)

        # ✅ event_type 다중 필터 (comma-separated)
        et = self.request.query_params.get("event_type")
        if et:
            types = [x for x in et.split(",") if x]
            if types:
                qs = qs.filter(event_type__in=types)

        return qs.order_by("-occurred_at", "-id")

    # --------------------------------------------------
    # Risk Top
    # --------------------------------------------------
    @action(detail=False, methods=["get"], url_path="risk")
    def risk(self, request):
        video_id = request.query_params.get("video")
        if not video_id:
            return Response({"detail": "video is required"}, status=400)

        limit = int(request.query_params.get("limit") or 5)
        range_key = request.query_params.get("range", "24h")
        since = _range_to_since(range_key)

        qs = VideoPlaybackEvent.objects.filter(video_id=video_id).select_related(
            "enrollment", "enrollment__student"
        )
        if since:
            qs = qs.filter(occurred_at__gte=since)

        agg = {}
        for ev in qs.iterator():
            eid = ev.enrollment_id
            if eid not in agg:
                agg[eid] = {
                    "enrollment_id": eid,
                    "student_name": ev.enrollment.student.name,
                    "score": 0,
                    "danger": 0,
                    "warn": 0,
                    "info": 0,
                    "last_occurred_at": None,
                }

            s = _event_score(ev.event_type, bool(ev.violated), ev.violation_reason)
            agg[eid]["score"] += s

            if ev.violated:
                agg[eid]["danger"] += 1
            elif ev.event_type in ("SEEK_ATTEMPT", "SPEED_CHANGE_ATTEMPT", "FOCUS_LOST"):
                agg[eid]["warn"] += 1
            else:
                agg[eid]["info"] += 1

            if (
                agg[eid]["last_occurred_at"] is None
                or ev.occurred_at > agg[eid]["last_occurred_at"]
            ):
                agg[eid]["last_occurred_at"] = ev.occurred_at

        rows = sorted(
            agg.values(),
            key=lambda r: (r["score"], r["danger"], r["warn"]),
            reverse=True,
        )[:limit]

        return Response(VideoRiskRowSerializer(rows, many=True).data)

    # --------------------------------------------------
    # CSV Export
    # --------------------------------------------------
    @action(detail=False, methods=["get"], url_path="export")
    def export_csv(self, request):
        video_id = request.query_params.get("video")
        if not video_id:
            return Response({"detail": "video is required"}, status=400)

        range_key = request.query_params.get("range", "24h")
        since = _range_to_since(range_key)

        qs = VideoPlaybackEvent.objects.filter(video_id=video_id).select_related(
            "enrollment", "enrollment__student"
        )
        if since:
            qs = qs.filter(occurred_at__gte=since)

        qs = qs.order_by("-occurred_at", "-id")

        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = (
            f'attachment; filename="video_{video_id}_events_{range_key}.csv"'
        )

        writer = csv.writer(resp)
        writer.writerow([
            "occurred_at",
            "student_name",
            "enrollment_id",
            "event_type",
            "violated",
            "violation_reason",
            "session_id",
            "user_id",
            "score",
            "payload",
        ])

        for ev in qs.iterator():
            writer.writerow([
                ev.occurred_at.isoformat(),
                ev.enrollment.student.name if ev.enrollment_id else "",
                ev.enrollment_id,
                ev.event_type,
                "Y" if ev.violated else "N",
                ev.violation_reason or "",
                ev.session_id,
                ev.user_id,
                _event_score(ev.event_type, bool(ev.violated), ev.violation_reason),
                ev.event_payload,
            ])

        return resp
