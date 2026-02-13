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
    VideoAccess,
    AccessMode,
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
from ..services.playback_session import init_session_redis  # Redis 세션 초기화 (선택적)
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
    - 불일치 시 즉시 차단
    - access_mode도 검증 (정책 변경 시 토큰 무효화)
    """
    try:
        video_id = int(payload.get("video_id"))
        enrollment_id = int(payload.get("enrollment_id"))
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

    if pv != current:
        return False

    # Validate access_mode consistency
    try:
        from apps.domains.enrollment.models import Enrollment
        from ..services.access_resolver import get_effective_access_mode
        
        enrollment = Enrollment.objects.filter(id=enrollment_id).first()
        if enrollment:
            current_access_mode = get_effective_access_mode(video=v, enrollment=enrollment)
            token_access_mode = payload.get("access_mode")
            
            if token_access_mode and token_access_mode != current_access_mode.value:
                return False
    except Exception:
        # If access_mode validation fails, still allow (backward compatibility)
        pass

    return True


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

        # Resolve access mode BEFORE creating session
        from ..services.access_resolver import get_effective_access_mode
        access_mode = get_effective_access_mode(video=video, enrollment=enrollment)
        
        # Determine if monitoring is required
        monitoring_enabled = (access_mode == AccessMode.PROCTORED_CLASS)
        
        session_id = None
        expires_at = None
        
        # Only create DB session for PROCTORED_CLASS
        student_id = enrollment.student_id
        if monitoring_enabled:
            ok, sess, err = issue_session(
                student_id=student_id,
                device_id=device_id,
                ttl_seconds=ttl,
                max_sessions=int(getattr(settings, "VIDEO_MAX_SESSIONS", 9999)),
                max_devices=int(getattr(settings, "VIDEO_MAX_DEVICES", 9999)),
            )

            if not ok:
                return Response({"detail": err}, status=409)

            session_id = sess["session_id"]
            expires_at_timestamp = int(sess["expires_at"])
            expires_at = timezone.datetime.fromtimestamp(expires_at_timestamp, tz=timezone.utc)

            VideoPlaybackSession.objects.create(
                video=video,
                enrollment=enrollment,
                session_id=session_id,
                device_id=device_id,
                status=VideoPlaybackSession.Status.ACTIVE,
                started_at=timezone.now(),
                expires_at=expires_at,
                last_seen=timezone.now(),
                violated_count=0,
                total_count=0,
                is_revoked=False,
            )
            init_session_redis(session_id=session_id, ttl_seconds=ttl)
        else:
            # FREE_REVIEW: No DB session, calculate expires_at for token only
            expires_at = timezone.now() + timezone.timedelta(seconds=ttl)

        perm = self._load_permission(video=video, enrollment=enrollment)
        policy = self._effective_policy(video=video, enrollment=enrollment, perm=perm)

        # ✅ token에 pv, access_mode, monitoring_enabled, student_id 포함
        token = create_playback_token(
            payload={
                "video_id": video.id,
                "enrollment_id": enrollment.id,
                "session_id": session_id,  # None for FREE_REVIEW
                "user_id": request.user.id,
                "student_id": student_id,
                "access_mode": access_mode.value,
                "monitoring_enabled": monitoring_enabled,
                "pv": _policy_version_of(video),
                "rid": request_id,
            },
            ttl_seconds=ttl,
        )

        play_url = self._public_play_url(
            video=video,
            expires_at=int(expires_at.timestamp()) if expires_at else int(timezone.now().timestamp()) + ttl,
            user_id=request.user.id,
        )

        resp = Response(
            PlaybackResponseSerializer({
                "token": token,
                "session_id": session_id,
                "expires_at": int(expires_at.timestamp()) if expires_at else None,
                "access_mode": access_mode.value,
                "monitoring_enabled": monitoring_enabled,
                "policy": policy,
                "play_url": play_url,
            }).data,
            status=201,
        )

        # Set signed cookies only if we have expires_at
        if expires_at:
            self._set_signed_cookies(resp, video_id=video.id, expires_at=int(expires_at.timestamp()))
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

        # FREE_REVIEW: Skip DB operations (session_id=null, no DB)
        monitoring_enabled = payload.get("monitoring_enabled")
        if monitoring_enabled is None:
            monitoring_enabled = bool(payload.get("session_id"))
        if not monitoring_enabled:
            return Response({"ok": True})

        sid = str(payload.get("session_id") or "")
        student_id = int(payload.get("student_id") or payload.get("user_id", 0))
        if sid:
            st = _session_db_status(sid)
            if _db_session_is_inactive(st):
                return Response({"detail": "session_inactive"}, status=409)

        if not is_session_active(
            student_id=student_id,
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

        # FREE_REVIEW: Skip DB operations
        monitoring_enabled = payload.get("monitoring_enabled")
        if monitoring_enabled is None:
            monitoring_enabled = bool(payload.get("session_id"))
        if not monitoring_enabled:
            return Response({"ok": True})

        sid = str(payload.get("session_id") or "")
        student_id = int(payload.get("student_id") or payload.get("user_id", 0))
        if sid:
            st = _session_db_status(sid)
            if _db_session_is_inactive(st):
                return Response({"detail": "session_inactive"}, status=409)

        ok2 = heartbeat_session(
            student_id=student_id,
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

        # FREE_REVIEW: Skip DB operations
        monitoring_enabled = payload.get("monitoring_enabled")
        if monitoring_enabled is None:
            monitoring_enabled = bool(payload.get("session_id"))
        if monitoring_enabled:
            session_id = str(payload.get("session_id") or "")
            student_id = int(payload.get("student_id") or payload.get("user_id", 0))
            if session_id:
                end_session(
                    student_id=student_id,
                    session_id=session_id,
                )

                VideoPlaybackSession.objects.filter(
                    session_id=session_id
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

        # FREE_REVIEW: Skip all DB operations
        monitoring_enabled = payload.get("monitoring_enabled")
        if monitoring_enabled is None:
            monitoring_enabled = bool(payload.get("session_id"))
        if not monitoring_enabled:
            return Response(
                PlaybackEventBatchResponseSerializer({"stored": 0}).data,
                status=201,
            )

        user_id = int(payload["user_id"])
        student_id = int(payload.get("student_id") or user_id)
        session_id = str(payload["session_id"])

        # DB 상태 차단
        st = _session_db_status(session_id)
        if _db_session_is_inactive(st):
            return Response({"detail": "session_inactive"}, status=409)

        # 세션 활성 상태 확인 (DB 기반)
        if not is_session_active(student_id=student_id, session_id=session_id):
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
            perm = VideoAccess.objects.filter(video=video, enrollment=enrollment).first()

        policy_snapshot = {}
        try:
            if video and enrollment:
                m = VideoPlaybackMixin()
                policy_snapshot = m._effective_policy(video=video, enrollment=enrollment, perm=perm)
        except Exception:
            policy_snapshot = {}

        def _is_violation(ev_type: str, snap: dict) -> tuple[bool, str]:
            """
            ✅ 최소 강제 위반 판정(서버 단):
            - Violation logic ONLY applies when access_mode == PROCTORED_CLASS
            - This function is only called when monitoring_enabled == True
            - seek blocked/bounded 환경에서 SEEK_ATTEMPT는 violated
            - speed 제한 환경에서 SPEED_CHANGE_ATTEMPT는 violated
            """
            # Double-check access mode (should already be PROCTORED_CLASS if we're here)
            access_mode_value = (snap or {}).get("access_mode")
            if access_mode_value != AccessMode.PROCTORED_CLASS.value:
                # Safety check: no violations in FREE_REVIEW mode
                return False, ""
            
            # Only check violations for PROCTORED_CLASS
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
        # - 각 이벤트마다 DB 카운터 갱신 → batch 쪼개기 우회 불가
        latest_stats = None
        revoke_reason = ""

        for e in events:
            ev_type = e["type"]
            ev_payload = e.get("payload", {}) or {}

            violated, reason = _is_violation(ev_type, policy_snapshot)
            if violated and reason:
                revoke_reason = reason

            # ✅ DB 누적 갱신
            latest_stats = record_session_event(
                student_id=student_id,
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

        # 트랜잭션 범위 최소화: bulk_create와 통계 업데이트 분리 (50명 원장 확장 대비)
        # 긴 트랜잭션은 DB 전체를 멈출 수 있음
        with transaction.atomic():
            VideoPlaybackEvent.objects.bulk_create(objs, batch_size=500)
        
        # 통계 업데이트는 별도 트랜잭션으로 분리 (lock 시간 단축)
        stats = latest_stats or get_session_violation_stats(session_id=session_id)
        violated_cnt = int(stats.get("violated") or 0)
        total_cnt = int(stats.get("total") or 0)

        if should_revoke_by_stats(violated=violated_cnt, total=total_cnt):
            with transaction.atomic():
                revoke_session(student_id=student_id, session_id=session_id)

        return Response(
            PlaybackEventBatchResponseSerializer({"stored": len(objs)}).data,
            status=201,
        )
