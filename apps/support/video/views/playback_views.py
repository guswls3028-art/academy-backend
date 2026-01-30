# PATH: apps/support/video/views/playback_views.py

import uuid

from django.conf import settings
from django.utils import timezone
from django.db import transaction

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
    revoke_session,
    record_session_event,
    get_session_violation_stats,
    should_revoke_by_stats,
)
from .playback_mixin import VideoPlaybackMixin


# ----------------------------------------------------------
# internal helpers (원본 구조 유지: view 내부 보조 함수로만 추가)
# ----------------------------------------------------------

def _req_id() -> str:
    return uuid.uuid4().hex


def _policy_version_of(video: Video) -> int:
    try:
        return int(getattr(video, "policy_version", 1) or 1)
    except Exception:
        return 1


def _is_policy_token_valid(payload: dict) -> bool:
    """
    token payload의 pv와 현재 video.policy_version 비교.
    - 불일치 시 즉시 차단 (문제 6)
    """
    try:
        video_id = int(payload.get("video_id"))
    except Exception:
        return False

    # NOTE: migrations 적용 전에는 policy_version 컬럼이 없으면 SELECT에서 터질 수 있음.
    # 실서비스는 migration 이후를 전제로 한다.
    v = Video.objects.filter(id=video_id).only("id", "policy_version").first()
    if not v:
        return False

    current = _policy_version_of(v)
    try:
        pv = int(payload.get("pv") or 0)
    except Exception:
        pv = 0

    return pv == current


def _deny(detail: str, *, code=status.HTTP_403_FORBIDDEN):
    return Response({"detail": detail}, status=code)


def _session_db_status(session_id: str):
    return (
        VideoPlaybackSession.objects
        .filter(session_id=session_id)
        .values_list("status", flat=True)
        .first()
    )


def _db_session_is_inactive(st: str | None) -> bool:
    return st in (VideoPlaybackSession.Status.REVOKED, VideoPlaybackSession.Status.EXPIRED)


# ==========================================================
# Playback Start
# ==========================================================

class PlaybackStartView(VideoPlaybackMixin, APIView):
    permission_classes = [IsAuthenticated, IsStudent]

    def post(self, request):
        request_id = _req_id()

        serializer = PlaybackStartRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        enrollment_id = serializer.validated_data["enrollment_id"]
        device_id = serializer.validated_data["device_id"]

        video_id = request.data.get("video_id")
        if not video_id:
            return Response({"detail": "video_id_required"}, status=400)

        enrollment = Enrollment.objects.select_related(
            "student",
            "lecture",
        ).get(id=enrollment_id, status="ACTIVE")

        video = Video.objects.select_related(
            "session",
            "session__lecture",
        ).get(id=int(video_id))

        # 수강 검증
        if enrollment.lecture_id != video.session.lecture_id:
            return _deny("enrollment_mismatch", code=403)

        if not SessionEnrollment.objects.filter(
            session=video.session,
            enrollment=enrollment,
        ).exists():
            return _deny("no_session_access", code=403)

        ok, reason = self._check_access(video=video, enrollment=enrollment)
        if not ok:
            return _deny(reason, code=403)

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

        # ✅ token에 pv(policy_version) 포함
        token = create_playback_token(
            payload={
                "video_id": video.id,
                "enrollment_id": enrollment.id,
                "session_id": session_id,
                "user_id": request.user.id,
                "pv": _policy_version_of(video),
                "rid": request_id,  # trace
            },
            ttl_seconds=ttl,
        )

        play_url = self._public_play_url(
            video=video,
            expires_at=expires_at,
            user_id=request.user.id,
        )

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
            return _deny(err, code=403)

        if not _is_policy_token_valid(payload):
            return _deny("policy_changed", code=403)

        sid = str(payload.get("session_id") or "")
        if sid:
            st = _session_db_status(sid)
            if _db_session_is_inactive(st):
                return Response({"detail": "session_inactive"}, status=409)

        if not is_session_active(
            user_id=int(payload["user_id"]),
            session_id=str(payload["session_id"]),
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
            return _deny(err, code=403)

        if not _is_policy_token_valid(payload):
            return _deny("policy_changed", code=403)

        sid = str(payload.get("session_id") or "")
        if sid:
            st = _session_db_status(sid)
            if _db_session_is_inactive(st):
                return Response({"detail": "session_inactive"}, status=409)

        ok2 = heartbeat_session(
            user_id=int(payload["user_id"]),
            session_id=str(payload["session_id"]),
            ttl_seconds=int(getattr(settings, "VIDEO_PLAYBACK_TTL_SECONDS", 600)),
        )
        if not ok2:
            return Response({"detail": "session_inactive"}, status=409)

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
            return _deny(err, code=403)

        end_session(
            user_id=int(payload["user_id"]),
            session_id=str(payload["session_id"]),
        )

        VideoPlaybackSession.objects.filter(
            session_id=str(payload["session_id"])
        ).update(
            status=VideoPlaybackSession.Status.ENDED,
            ended_at=timezone.now(),
        )

        return Response({"ok": True})


# ==========================================================
# Event Batch
# ==========================================================

class PlaybackEventBatchView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PlaybackEventBatchRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        ok, payload, err = verify_playback_token(serializer.validated_data["token"])
        if not ok:
            return _deny(err, code=403)

        if not _is_policy_token_valid(payload):
            return _deny("policy_changed", code=403)

        user_id = int(payload["user_id"])
        session_id = str(payload["session_id"])

        # DB 상태 차단
        st = _session_db_status(session_id)
        if _db_session_is_inactive(st):
            return Response({"detail": "session_inactive"}, status=409)

        # Redis 상태 차단
        if not is_session_active(user_id=user_id, session_id=session_id):
            return Response({"detail": "session_inactive"}, status=409)

        events = serializer.validated_data["events"]

        # 폭주 방지
        max_batch = int(getattr(settings, "VIDEO_EVENT_BATCH_MAX", 200))
        if len(events) > max_batch:
            return Response({"detail": "batch_too_large"}, status=413)

        now = timezone.now()
        objs = []

        # policy snapshot 계산 (원본 믹스인 재사용)
        video = Video.objects.filter(id=int(payload["video_id"])).first()
        enrollment = Enrollment.objects.filter(id=int(payload["enrollment_id"])).first()
        perm = None
        if video and enrollment:
            perm = VideoPermission.objects.filter(video=video, enrollment=enrollment).first()

        policy_snapshot = {}
        try:
            if video:
                m = VideoPlaybackMixin()
                policy_snapshot = m._effective_policy(video=video, perm=perm)
        except Exception:
            policy_snapshot = {}

        def _is_violation(ev_type: str, snap: dict) -> tuple[bool, str]:
            """
            ✅ 최소 강제 위반 판정(서버 단):
            - seek blocked/bounded 환경에서 SEEK_ATTEMPT는 violated
            - speed 제한 환경에서 SPEED_CHANGE_ATTEMPT는 violated
            (추가 강화는 여기만 수정하면 됨 → 구조 유지)
            """
            if ev_type == "SEEK_ATTEMPT":
                seek = (snap or {}).get("seek") or {}
                allow_seek = bool((snap or {}).get("allow_seek", True))
                mode = seek.get("mode")
                if (not allow_seek) or mode in ("blocked", "bounded_forward"):
                    return True, f"seek_{mode or 'blocked'}"
            if ev_type == "SPEED_CHANGE_ATTEMPT":
                pr = ((snap or {}).get("playback_rate") or {})
                ui = bool(pr.get("ui_control", True))
                mx = float(pr.get("max", 1.0) or 1.0)
                if (not ui) or mx <= 1.0:
                    return True, "speed_blocked"
            return False, ""

        # ✅ 세션 단위 누적 위반 판단
        # - 각 이벤트마다 Redis 카운터 갱신 → batch 쪼개기 우회 불가
        latest_stats = None
        revoke_reason = ""

        for e in events:
            ev_type = e["type"]
            ev_payload = e.get("payload", {}) or {}

            violated, reason = _is_violation(ev_type, policy_snapshot)
            if violated and reason:
                revoke_reason = reason

            # ✅ Redis 누적 갱신
            latest_stats = record_session_event(
                user_id=user_id,
                session_id=session_id,
                violated=bool(violated),
                reason=reason or "",
            )

            objs.append(
                VideoPlaybackEvent(
                    video_id=int(payload["video_id"]),
                    enrollment_id=int(payload["enrollment_id"]),
                    session_id=session_id,
                    user_id=user_id,
                    event_type=ev_type,
                    event_payload=ev_payload,
                    policy_snapshot=policy_snapshot,
                    violated=bool(violated),
                    violation_reason=reason or "",
                    occurred_at=now,
                )
            )

        with transaction.atomic():
            VideoPlaybackEvent.objects.bulk_create(objs, batch_size=500)

            # ✅ 누적 기준으로 revoke 결정
            stats = latest_stats or get_session_violation_stats(session_id=session_id)
            violated_cnt = int(stats.get("violated") or 0)
            total_cnt = int(stats.get("total") or 0)

            if should_revoke_by_stats(violated=violated_cnt, total=total_cnt):
                # Redis 세션 즉시 제거
                revoke_session(user_id=user_id, session_id=session_id)

                # DB 반영
                VideoPlaybackSession.objects.filter(session_id=session_id).update(
                    status=VideoPlaybackSession.Status.REVOKED,
                    ended_at=timezone.now(),
                )

        return Response(
            PlaybackEventBatchResponseSerializer({"stored": len(objs)}).data,
            status=201,
        )
