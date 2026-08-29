
import uuid

from django.conf import settings
from django.utils import timezone
from django.db import transaction

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from apps.core.permissions import IsStudent
from apps.support.student_app.video_media import issue_playback_access_grant
from academy.application.use_cases.student_video_access_context import (
    StudentVideoAccessError,
    resolve_student_video_access_context,
)

from ..models import (
    Video,
    VideoPlaybackSession,
    VideoPlaybackEvent,
    AccessMode,
)
from academy.adapters.db.django import repositories_video as video_repo
from ..serializers import (
    PlaybackStartRequestSerializer,
    PlaybackRefreshRequestSerializer,
    PlaybackHeartbeatRequestSerializer,
    PlaybackEndRequestSerializer,
    PlaybackResponseSerializer,
    PlaybackEventBatchRequestSerializer,
    PlaybackEventBatchResponseSerializer,
)
from ..drm import verify_playback_token
from ..services.playback_session import (
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
    - 불일치 시 즉시 차단
    - access_mode도 검증 (정책 변경 시 토큰 무효화)
    """
    direct_audience = payload.get("aud") == "student-video-direct"
    direct_source = payload.get("access_source") == "DIRECT_VIDEO_ENTITLEMENT"
    if direct_audience or direct_source:
        if not (direct_audience and direct_source):
            return False
        try:
            tenant_id = int(payload.get("tenant_id"))
            student_id = int(payload.get("student_id"))
            video_id = int(payload.get("video_id"))
            entitlement_id = int(payload.get("direct_entitlement_id"))
            pv = int(payload.get("pv") or 0)
        except (TypeError, ValueError):
            return False
        if min(tenant_id, student_id, video_id, entitlement_id) <= 0:
            return False
        if payload.get("access_mode") != AccessMode.FREE_REVIEW.value:
            return False
        try:
            from apps.core.models import Tenant
            from apps.domains.video.services.direct_entitlements import (
                DirectVideoEntitlementError,
                lock_and_revalidate_direct_video_access,
            )

            tenant = Tenant.objects.get(id=tenant_id, is_active=True)
            locked = lock_and_revalidate_direct_video_access(
                tenant=tenant,
                student_id=student_id,
                video_id=video_id,
                entitlement_id=entitlement_id,
            )
        except (Tenant.DoesNotExist, DirectVideoEntitlementError):
            return False
        return pv == _policy_version_of(locked.video)

    try:
        video_id = int(payload.get("video_id"))
        enrollment_id = int(payload.get("enrollment_id"))
    except Exception:
        return False

    try:
        v = video_repo.video_get_by_id_with_relations(video_id)
    except Exception:
        return False
    lecture = getattr(getattr(v, "session", None), "lecture", None)
    if getattr(v, "deleted_at", None) is not None:
        return False
    if not lecture or not (
        getattr(lecture, "is_active", False)
        or getattr(lecture, "is_system", False)
    ):
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
        from ..services.access_resolver import get_effective_access_mode

        enrollment = video_repo.enrollment_get_by_id_with_relations(enrollment_id)
        if enrollment.lecture_id != getattr(v.session, "lecture_id", None):
            return False
        current_access_mode = get_effective_access_mode(video=v, enrollment=enrollment)
        if current_access_mode == AccessMode.BLOCKED:
            return False
        token_access_mode = payload.get("access_mode")
        if token_access_mode and token_access_mode != current_access_mode.value:
            return False
    except Exception:
        return False

    return True


def _deny(detail: str, *, code=status.HTTP_403_FORBIDDEN):
    return Response({"detail": detail}, status=code)


def _playback_token_request_error(payload: dict, request) -> str | None:
    try:
        if int(payload.get("user_id")) != int(request.user.id):
            return "token_user_mismatch"
    except (AttributeError, TypeError, ValueError):
        return "token_user_mismatch"

    tenant = getattr(request, "tenant", None)
    if not tenant:
        return "token_tenant_mismatch"
    token_tenant_id = payload.get("tenant_id")
    if token_tenant_id is not None:
        try:
            return None if int(token_tenant_id) == int(tenant.id) else "token_tenant_mismatch"
        except (TypeError, ValueError):
            return "token_tenant_mismatch"

    # Rolling compatibility for already-issued tokens: bind them to the video's
    # authoritative tenant. New tokens always carry tenant_id.
    try:
        video = video_repo.video_get_by_id_with_relations(int(payload.get("video_id")))
    except (TypeError, ValueError, Video.DoesNotExist):
        return "token_tenant_mismatch"
    if not video:
        return "token_tenant_mismatch"
    video_tenant_id = getattr(video, "tenant_id", None)
    if video_tenant_id is None:
        video_tenant_id = getattr(
            getattr(getattr(video, "session", None), "lecture", None),
            "tenant_id",
            None,
        )
    try:
        return None if int(video_tenant_id) == int(tenant.id) else "token_tenant_mismatch"
    except (TypeError, ValueError):
        return "token_tenant_mismatch"


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

        raw_video_id = request.data.get("video_id")
        if raw_video_id in (None, ""):
            return Response({"detail": "video_id_required"}, status=400)
        try:
            video_id = int(raw_video_id)
            if video_id <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return Response({"detail": "video_id_invalid"}, status=400)

        try:
            video = video_repo.video_get_by_id_with_relations(video_id)
        except Video.DoesNotExist:
            video = None
        if not video:
            return Response({"detail": "video_not_found"}, status=404)

        try:
            access_context = resolve_student_video_access_context(
                request,
                video,
                explicit_enrollment_id=enrollment_id,
            )
        except StudentVideoAccessError as exc:
            return _deny(exc.detail, code=exc.status_code)

        enrollment = access_context.enrollment
        if enrollment is None:
            return _deny("enrollment_required", code=403)

        # Tenant isolation: enrollment and video must belong to request.tenant
        if enrollment.lecture.tenant_id != request.tenant.id:
            return _deny("tenant_mismatch", code=403)
        if video.session.lecture.tenant_id != request.tenant.id:
            return _deny("tenant_mismatch", code=403)

        # 공개 영상: 같은 테넌트(프로그램)에 등록된 학생이면 시청 가능
        from apps.domains.video.models import Video as _Video
        is_public_video = getattr(video, "visibility", _Video.Visibility.ENROLLED) == _Video.Visibility.PUBLIC
        if is_public_video:
            video_tenant_id = getattr(video, "tenant_id", None)
            if video_tenant_id is None:
                video_tenant_id = (
                    video.session.lecture.tenant_id if video.session and video.session.lecture else None
                )
            if enrollment.lecture.tenant_id != video_tenant_id:
                return _deny("tenant_mismatch", code=403)
        else:
            if enrollment.lecture_id != video.session.lecture_id:
                return _deny("enrollment_mismatch", code=403)

            if not video_repo.session_enrollment_exists(video.session, enrollment):
                return _deny("no_session_access", code=403)

        ok, reason = self._check_access(video=video, enrollment=enrollment)
        if not ok:
            return _deny(reason, code=403)

        grant = issue_playback_access_grant(
            video=video,
            enrollment=enrollment,
            user=request.user,
            device_id=device_id,
            request_id=request_id,
        )
        if not grant.token or not grant.access_mode:
            return _deny(
                grant.error or "access_denied",
                code=grant.status_code,
            )

        access_mode = AccessMode(grant.access_mode)
        monitoring_enabled = grant.monitoring_enabled
        session_id = grant.session_id
        expires_at = grant.expires_at

        perm = self._load_permission(video=video, enrollment=enrollment)
        policy = self._effective_policy(video=video, enrollment=enrollment, perm=perm)

        play_url = self._public_play_url(
            video=video,
            expires_at=expires_at,
            user_id=request.user.id,
        )

        resp = Response(
            PlaybackResponseSerializer({
                "token": grant.token,
                "session_id": session_id,
                "expires_at": expires_at,
                "access_mode": access_mode.value,
                "monitoring_enabled": monitoring_enabled,
                "policy": policy,
                "play_url": play_url,
            }).data,
            status=201,
        )

        # Set signed cookies only if we have expires_at
        if expires_at:
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
        binding_error = _playback_token_request_error(payload, request)
        if binding_error:
            return _deny(binding_error, code=403)

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
        binding_error = _playback_token_request_error(payload, request)
        if binding_error:
            return _deny(binding_error, code=403)

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
        binding_error = _playback_token_request_error(payload, request)
        if binding_error:
            return _deny(binding_error, code=403)

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
        binding_error = _playback_token_request_error(payload, request)
        if binding_error:
            return _deny(binding_error, code=403)

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
        video = video_repo.video_get_by_id(int(payload["video_id"]))
        enrollment = video_repo.enrollment_get_by_id(int(payload["enrollment_id"]))
        perm = video_repo.video_access_get(video, enrollment) if video and enrollment else None

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
                if (not allow_seek) or mode in ("blocked", "bounded_forward", "budgeted_forward"):
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
            video_repo.playback_event_bulk_create(objs, batch_size=500)
        
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
