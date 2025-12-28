# apps/support/media/views/playback_views.py

import time

from django.conf import settings
from django.utils import timezone

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from apps.core.permissions import IsStudent
from apps.domains.enrollment.models import Enrollment, SessionEnrollment

from ..models import (
    Video,
    VideoPlaybackSession,
    VideoPlaybackEvent,
    VideoProgress,
    VideoPermission,
)
from ..serializers import (
    PlaybackStartRequestSerializer,
    PlaybackRefreshRequestSerializer,
    PlaybackHeartbeatRequestSerializer,
    PlaybackEndRequestSerializer,
    PlaybackResponseSerializer,
    PlaybackEventBatchRequestSerializer,
    PlaybackEventBatchResponseSerializer,
)
from ..drm import create_playback_token, verify_playback_token
from ..services.playback_session import (
    issue_session,
    heartbeat_session,
    end_session,
    is_session_active,
)
from .playback_mixin import VideoPlaybackMixin


# ==========================================================
# Playback Start
# ==========================================================

class PlaybackStartView(VideoPlaybackMixin, APIView):
    permission_classes = [IsAuthenticated, IsStudent]

    def post(self, request):
        serializer = PlaybackStartRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        enrollment_id = serializer.validated_data["enrollment_id"]
        device_id = serializer.validated_data["device_id"]

        enrollment = Enrollment.objects.select_related(
            "student",
            "lecture",
        ).get(id=enrollment_id, status="ACTIVE")

        video = Video.objects.select_related(
            "session",
            "session__lecture",
        ).get(id=request.data.get("video_id"))

        # 수강 검증
        if enrollment.lecture_id != video.session.lecture_id:
            return Response({"detail": "enrollment_mismatch"}, status=403)

        if not SessionEnrollment.objects.filter(
            session=video.session,
            enrollment=enrollment,
        ).exists():
            return Response({"detail": "no_session_access"}, status=403)

        ok, reason = self._check_access(video=video, enrollment=enrollment)
        if not ok:
            return Response({"detail": reason}, status=403)

        ttl = int(getattr(settings, "VIDEO_PLAYBACK_TTL_SECONDS", 600))

        ok, sess, err = issue_session(
            user_id=request.user.id,
            device_id=device_id,
            ttl_seconds=ttl,
            max_sessions=int(getattr(settings, "VIDEO_MAX_SESSIONS", 9999)),
            max_devices=int(getattr(settings, "VIDEO_MAX_DEVICES", 9999)),
        )

        if not ok:
            return Response({"detail": err}, status=409)

        session_id = sess["session_id"]
        expires_at = sess["expires_at"]

        VideoPlaybackSession.objects.create(
            video=video,
            enrollment=enrollment,
            session_id=session_id,
            device_id=device_id,
            status=VideoPlaybackSession.Status.ACTIVE,
            started_at=timezone.now(),
        )

        perm = self._load_permission(video=video, enrollment=enrollment)
        policy = self._effective_policy(video=video, perm=perm)

        token = create_playback_token(
            payload={
                "video_id": video.id,
                "enrollment_id": enrollment.id,
                "session_id": session_id,
                "user_id": request.user.id,
            },
            ttl_seconds=ttl,
        )

        play_url = self._public_play_url(video.id)

        resp = Response(
            PlaybackResponseSerializer({
                "token": token,
                "session_id": session_id,
                "expires_at": expires_at,
                "policy": policy,
                "play_url": play_url,
            }).data,
            status=201,
        )

        self._set_signed_cookies(resp, video_id=video.id, expires_at=expires_at)
        return resp


# ==========================================================
# Playback Refresh
# ==========================================================

class PlaybackRefreshView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PlaybackRefreshRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        ok, payload, err = verify_playback_token(serializer.validated_data["token"])
        if not ok:
            return Response({"detail": err}, status=403)

        if not is_session_active(
            user_id=payload["user_id"],
            session_id=payload["session_id"],
        ):
            return Response({"detail": "session_inactive"}, status=409)

        return Response({"ok": True})


# ==========================================================
# Playback Heartbeat
# ==========================================================

class PlaybackHeartbeatView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PlaybackHeartbeatRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        ok, payload, err = verify_playback_token(serializer.validated_data["token"])
        if not ok:
            return Response({"detail": err}, status=403)

        heartbeat_session(
            user_id=payload["user_id"],
            session_id=payload["session_id"],
            ttl_seconds=int(getattr(settings, "VIDEO_PLAYBACK_TTL_SECONDS", 600)),
        )

        return Response({"ok": True})


# ==========================================================
# Playback End
# ==========================================================

class PlaybackEndView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PlaybackEndRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        ok, payload, err = verify_playback_token(serializer.validated_data["token"])
        if not ok:
            return Response({"detail": err}, status=403)

        end_session(
            user_id=payload["user_id"],
            session_id=payload["session_id"],
        )

        VideoPlaybackSession.objects.filter(
            session_id=payload["session_id"]
        ).update(
            status=VideoPlaybackSession.Status.ENDED,
            ended_at=timezone.now(),
        )

        return Response({"ok": True})


# ==========================================================
# Event Batch (audit-only)
# ==========================================================

class PlaybackEventBatchView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PlaybackEventBatchRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        ok, payload, err = verify_playback_token(serializer.validated_data["token"])
        if not ok:
            return Response({"detail": err}, status=403)

        events = serializer.validated_data["events"]
        stored = 0

        for e in events:
            VideoPlaybackEvent.objects.create(
                video_id=payload["video_id"],
                enrollment_id=payload["enrollment_id"],
                session_id=payload["session_id"],
                user_id=payload["user_id"],
                event_type=e["type"],
                event_payload=e.get("payload", {}),
                occurred_at=timezone.now(),
            )
            stored += 1

        return Response(
            PlaybackEventBatchResponseSerializer({"stored": stored}).data,
            status=201,
        )
