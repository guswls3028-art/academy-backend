# apps/support/media/views.py

import time
from datetime import datetime
from uuid import uuid4
from pathlib import Path
import mimetypes

from django.conf import settings
from django.db import models, transaction
from django.http import  Http404, FileResponse
from django.utils import timezone
from django.views import View

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework import serializers


from django_filters.rest_framework import DjangoFilterBackend

from libs.s3_client.presign import create_presigned_put_url
from libs.s3_client.client import head_object

from .models import (
    Video,
    VideoPermission,
    VideoProgress,
    VideoPlaybackSession,
    VideoPlaybackEvent,
)

from apps.core.permissions import IsAdminOrStaff
from apps.core.authentication import CsrfExemptSessionAuthentication

from rest_framework_simplejwt.authentication import JWTAuthentication


from apps.domains.lectures.models import Session
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.attendance.models import Attendance

from .serializers import (
    VideoSerializer,
    VideoDetailSerializer,
    VideoPermissionSerializer,
    VideoProgressSerializer,
    PlaybackStartRequestSerializer,
    PlaybackRefreshRequestSerializer,
    PlaybackHeartbeatRequestSerializer,
    PlaybackEndRequestSerializer,
    PlaybackResponseSerializer,
    PlaybackEventBatchRequestSerializer,
    PlaybackEventBatchResponseSerializer,
    PlaybackSessionSerializer,
)

from apps.shared.tasks.media import process_video_media

from .drm import create_playback_token, verify_playback_token
from .services.playback_session import (
    issue_session,
    heartbeat_session,
    end_session,
    is_session_active,
    create_playback_session,  # ⭐ 추가 (학생 Facade API용)
)

from .cdn.cloudfront import build_signed_cookies_for_path, default_cookie_options

# ⭐ 학생 전용 Playback Facade API용 Permission
from apps.core.permissions import IsStudent
#from apps.domains.enrollment.permissions import HasEnrollmentAccess
#일단 ai가 지우래서 주석처리. 혹시 모르니 유지

from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied, ValidationError


#HLS
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

# 하래서함
from rest_framework.parsers import JSONParser


class VideoViewSet(ModelViewSet):
    """
    Video 관리 ViewSet

    - 기본: 로그인만 필요
    - 업로드/관리 action: 관리자 or 스태프만 가능
    """

    queryset = Video.objects.all().select_related("session", "session__lecture")
    serializer_class = VideoSerializer
    parser_classes = [JSONParser]

    authentication_classes = [
        JWTAuthentication,
        CsrfExemptSessionAuthentication,  # (관리자 admin 페이지용, 유지)
    ]
    permission_classes = [IsAuthenticated]

    # 🔐 관리자 전용 action 목록
    ADMIN_ONLY_ACTIONS = {
        "upload_init",
        "upload_complete",
        "retry",
        "complete",
        "presign",
        "create",
        "update",
        "partial_update",
        "destroy",
    }

    def get_permissions(self):
        if self.action in self.ADMIN_ONLY_ACTIONS:
            return [IsAuthenticated(), IsAdminOrStaff()]
        return [IsAuthenticated()]

    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["session", "status"]
    search_fields = ["title"]

    # --------------------------------------------------
    # [LEGACY] Presigned URL 발급
    # POST /media/videos/presign/
    # --------------------------------------------------
    @action(detail=False, methods=["post"], url_path="presign")
    def presign(self, request):
        session_id = request.data.get("session")
        filename = request.data.get("filename")

        if not session_id or not filename:
            return Response(
                {"detail": "session, filename required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ext = filename.split(".")[-1]
        key = f"videos/{session_id}/{uuid4()}.{ext}"

        upload_url = create_presigned_put_url(key=key)
        return Response({"upload_url": upload_url, "file_key": key})

    # --------------------------------------------------
    # [LEGACY] 업로드 완료 + Worker 트리거
    # POST /media/videos/complete/
    # --------------------------------------------------
    @transaction.atomic
    @action(detail=False, methods=["post"], url_path="complete")
    def complete(self, request):
        session_id = request.data.get("session")
        title = request.data.get("title")
        file_key = request.data.get("file_key")

        if not all([session_id, title, file_key]):
            return Response(
                {"detail": "session, title, file_key required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        exists, size = head_object(file_key)
        if not exists or size == 0:
            return Response(
                {"detail": "S3 object not found"},
                status=status.HTTP_409_CONFLICT,
            )

        session = Session.objects.get(id=session_id)
        order = (session.videos.aggregate(
            max_order=models.Max("order")
        ).get("max_order") or 0) + 1

        video = Video.objects.create(
            session=session,
            title=title,
            file_key=file_key,
            order=order,
            status=Video.Status.UPLOADED,
        )

        # 🔥 여기만 바뀜 (on_commit 제거)
        process_video_media.delay(video.id)

        return Response(VideoSerializer(video).data, status=201)

    # ==================================================
    # [NEW] Step 1) upload/init
    # POST /media/videos/upload/init/
    # ==================================================
    @transaction.atomic
    @action(detail=False, methods=["post"], url_path="upload/init")
    def upload_init(self, request):
        session_id = request.data.get("session")
        title = request.data.get("title")
        filename = request.data.get("filename")

        allow_skip = bool(request.data.get("allow_skip", False))
        max_speed = float(request.data.get("max_speed", 1.0) or 1.0)
        show_watermark = bool(request.data.get("show_watermark", True))

        if not session_id or not title or not filename:
            return Response(
                {"detail": "session, title, filename required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        session = Session.objects.get(id=session_id)
        order = (session.videos.aggregate(
            max_order=models.Max("order")
        ).get("max_order") or 0) + 1

        ext = (filename.split(".")[-1] if "." in filename else "mp4").lower()
        key = f"videos/{session_id}/{uuid4()}.{ext}"

        video = Video.objects.create(
            session=session,
            title=title,
            file_key=key,
            order=order,
            status=Video.Status.PENDING,
            allow_skip=allow_skip,
            max_speed=max_speed,
            show_watermark=show_watermark,
        )

        upload_url = create_presigned_put_url(key=key)

        return Response(
            {
                "video": VideoSerializer(video).data,
                "upload_url": upload_url,
                "file_key": key,
            },
            status=status.HTTP_201_CREATED,
        )

    # ==================================================
    # [NEW] Step 2) upload/complete
    # POST /media/videos/{id}/upload/complete/
    # ==================================================
    @transaction.atomic
    @action(detail=True, methods=["post"], url_path="upload/complete")
    def upload_complete(self, request, pk=None):
        video = self.get_object()

        if video.status != Video.Status.PENDING:
            return Response(
                {"detail": f"Invalid status: {video.status}"},
                status=status.HTTP_409_CONFLICT,
            )

        exists, size = head_object(video.file_key)
        if not exists or size == 0:
            return Response(
                {"detail": "S3 object not found"},
                status=status.HTTP_409_CONFLICT,
            )

        video.status = Video.Status.UPLOADED
        video.save(update_fields=["status"])

        # 🔥 여기만 핵심 (on_commit 제거)
        process_video_media.delay(video.id)    
    
        return Response(VideoSerializer(video).data, status=status.HTTP_200_OK)

    # --------------------------------------------------
    # FAILED 영상 재처리
    # --------------------------------------------------
    @transaction.atomic
    @action(detail=True, methods=["post"], url_path="retry")
    def retry(self, request, pk=None):
        video = self.get_object()

        if video.status not in (Video.Status.FAILED, Video.Status.UPLOADED):
            return Response(
                {"detail": f"Cannot retry video in status {video.status}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 🔥 여기만 변경 (on_commit 제거)
        process_video_media.delay(video.id)
        
        return Response({"detail": "Video reprocessing started"}, status=status.HTTP_202_ACCEPTED)

    # --------------------------------------------------
    # Stats
    # --------------------------------------------------
    @action(detail=True, methods=["get"], url_path="stats")
    def stats(self, request, pk=None):
        video = self.get_object()
        lecture = video.session.lecture

        enrollments = Enrollment.objects.filter(lecture=lecture)
        progresses = {p.enrollment_id: p for p in VideoProgress.objects.filter(video=video)}
        perms = {p.enrollment_id: p for p in VideoPermission.objects.filter(video=video)}
        attendance = {a.enrollment_id: a.status for a in Attendance.objects.filter(session=video.session)}

        students = []
        for e in enrollments:
            vp = progresses.get(e.id)
            perm = perms.get(e.id)

            students.append({
                "student_name": e.student.name,
                "progress": vp.progress if vp else 0,
                "completed": vp.completed if vp else False,
                "attendance_status": attendance.get(e.id),
                "rule": perm.rule if perm else "free",
            })

        return Response({"video": VideoDetailSerializer(video).data, "students": students})

    # =======================================================
    # Playback API (v1 + CDN Signed Cookie + 정책 override)
    # =======================================================

    def _get_student_for_user(self, request):
        return getattr(request.user, "student_profile", None)

    def _check_access(self, *, video: Video, enrollment: Enrollment) -> tuple[bool, str | None]:
        if video.status != Video.Status.READY:
            return False, "video_not_ready"

        if not SessionEnrollment.objects.filter(session=video.session, enrollment=enrollment).exists():
            return False, "no_session_access"

        perm = VideoPermission.objects.filter(video=video, enrollment=enrollment).first()
        rule = perm.rule if perm else "free"

        if rule == "blocked":
            return False, "blocked"

        if rule == "once":
            vp = VideoProgress.objects.filter(video=video, enrollment=enrollment).first()
            if vp and vp.completed:
                return False, "already_completed_once"

        return True, None

    def _load_permission(self, *, video: Video, enrollment: Enrollment) -> VideoPermission | None:
        return VideoPermission.objects.filter(video=video, enrollment=enrollment).first()

    # --------------------------------------------------
    # 정책 생성 (PATCH 반영: seek 정책 확장 + legacy 유지)
    # --------------------------------------------------
    def _effective_policy(self, *, video: Video, perm: VideoPermission | None) -> dict:
        allow_seek = bool(video.allow_skip)
        max_rate = float(video.max_speed or 1.0)
        watermark_enabled = bool(video.show_watermark)
        ui_speed_control = True

        # seek policy (new, front-driven)
        # - free: fully allowed
        # - bounded_forward: block forward-jumps beyond max_watched (+ grace)
        # - blocked: no seeking at all
        seek_policy = {
            "mode": "free",
            "forward_limit": None,
            "grace_seconds": 3,
        }

        if perm:
            if perm.allow_skip_override is not None:
                allow_seek = bool(perm.allow_skip_override)
            if perm.max_speed_override is not None:
                max_rate = float(perm.max_speed_override)
            if perm.show_watermark_override is not None:
                watermark_enabled = bool(perm.show_watermark_override)

            # 온라인/1회제한(once) 대상: 앞으로만 제한 (되돌리기 자유)
            if getattr(perm, "rule", None) == "once":
                seek_policy = {
                    "mode": "bounded_forward",
                    "forward_limit": "max_watched",
                    "grace_seconds": 3,
                }

            # 최우선 차단
            if getattr(perm, "block_seek", False):
                allow_seek = False
                seek_policy = {"mode": "blocked"}

            if getattr(perm, "block_speed_control", False):
                ui_speed_control = False
                max_rate = 1.0

        return {
            "allow_seek": allow_seek,  # legacy (deprecated)
            "seek": seek_policy,
            "playback_rate": {"max": max_rate, "ui_control": ui_speed_control},
            "watermark": {"enabled": watermark_enabled, "mode": "overlay", "fields": ["user_id"]},
            "concurrency": {
                "max_sessions": int(getattr(settings, "VIDEO_MAX_SESSIONS", 9999)),
                "max_devices": int(getattr(settings, "VIDEO_MAX_DEVICES", 9999)),
            },
        }

    def _hls_path_prefix_for_video(self, video_id: int) -> str:
        return f"/hls/videos/{video_id}/"

    def _public_play_url(self, video_id: int) -> str:
        cdn_base = getattr(settings, "CDN_HLS_BASE_URL", "").rstrip("/")
        path = f"/hls/videos/{video_id}/master.m3u8"
        return f"{cdn_base}{path}" if cdn_base else path

    def _set_signed_cookies(self, response: Response, *, video_id: int, expires_at: int) -> None:
        path_prefix = self._hls_path_prefix_for_video(video_id)
        cookies = build_signed_cookies_for_path(path_prefix=path_prefix, expires_at=expires_at)
        opts = default_cookie_options(path_prefix=path_prefix)

        max_age = max(0, int(expires_at - int(time.time())))
        for k, v in cookies.items():
            response.set_cookie(
                key=k,
                value=v,
                max_age=max_age,
                expires=None,
                **opts,
            )

    @action(
        detail=True,
        methods=["post"],
        url_path="play",
        permission_classes=[IsAuthenticated, IsStudent],
    )
    def play(self, request, pk=None):
        video = self.get_object()

        req = PlaybackStartRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)

        enrollment_id = req.validated_data["enrollment_id"]
        device_id = req.validated_data["device_id"]

        student = self._get_student_for_user(request)
        if student is None:
            return Response({"detail": "student_profile_not_linked"}, status=403)

        try:
            enrollment = Enrollment.objects.select_related("student", "lecture").get(
                id=enrollment_id,
                student=student,
                status="ACTIVE",
            )
        except Enrollment.DoesNotExist:
            return Response({"detail": "enrollment_not_found"}, status=404)

        ok, reason = self._check_access(video=video, enrollment=enrollment)
        if not ok:
            return Response({"detail": reason}, status=403)

        perm = self._load_permission(video=video, enrollment=enrollment)
        policy = self._effective_policy(video=video, perm=perm)

        ttl = int(getattr(settings, "VIDEO_PLAYBACK_TTL_SECONDS", 600))
        max_sessions = int(policy["concurrency"]["max_sessions"])
        max_devices = int(policy["concurrency"]["max_devices"])

        ok_sess, sess, err = issue_session(
            user_id=request.user.id,
            device_id=device_id,
            ttl_seconds=ttl,
            max_sessions=max_sessions,
            max_devices=max_devices,
        )
        if not ok_sess:
            return Response({"detail": err}, status=409)

        session_id = str(sess["session_id"])
        expires_at = int(sess["expires_at"])

        VideoPlaybackSession.objects.create(
            video=video,
            enrollment=enrollment,
            session_id=session_id,
            device_id=device_id,
            status=VideoPlaybackSession.Status.ACTIVE,
        )

        token = create_playback_token(
            payload={
                "tenant_id": None,
                "video_id": video.id,
                "enrollment_id": enrollment.id,
                "user_id": request.user.id,
                "device_id": device_id,
                "session_id": session_id,
                "policy": policy,
            },
            ttl_seconds=ttl,
        )

        resp = PlaybackResponseSerializer({
            "token": token,
            "session_id": session_id,
            "expires_at": expires_at,
            "policy": policy,
            "play_url": self._public_play_url(video.id),
        })

        response = Response(resp.data, status=200)
        self._set_signed_cookies(response, video_id=video.id, expires_at=expires_at)
        return response

    @action(
        detail=True,
        methods=["post"],
        url_path="refresh",
        permission_classes=[IsAuthenticated, IsStudent],
    )
    def refresh(self, request, pk=None):
        video = self.get_object()

        req = PlaybackRefreshRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        token = req.validated_data["token"]

        ok, payload, err = verify_playback_token(token)
        if not ok:
            return Response({"detail": err}, status=401)

        if int(payload.get("video_id") or 0) != int(video.id):
            return Response({"detail": "video_mismatch"}, status=401)
        if int(payload.get("user_id") or 0) != int(request.user.id):
            return Response({"detail": "user_mismatch"}, status=401)

        session_id = str(payload.get("session_id") or "")
        device_id = str(payload.get("device_id") or "")
        enrollment_id = int(payload.get("enrollment_id") or 0)

        ttl = int(getattr(settings, "VIDEO_PLAYBACK_TTL_SECONDS", 600))
        ok_hb = heartbeat_session(user_id=request.user.id, session_id=session_id, ttl_seconds=ttl)
        if not ok_hb:
            return Response({"detail": "session_not_active"}, status=409)

        student = self._get_student_for_user(request)
        if student is None:
            return Response({"detail": "student_profile_not_linked"}, status=403)

        try:
            enrollment = Enrollment.objects.get(id=enrollment_id, student=student, status="ACTIVE")
        except Enrollment.DoesNotExist:
            return Response({"detail": "enrollment_not_found"}, status=404)

        ok_access, reason = self._check_access(video=video, enrollment=enrollment)
        if not ok_access:
            return Response({"detail": reason}, status=403)

        perm = self._load_permission(video=video, enrollment=enrollment)
        policy = self._effective_policy(video=video, perm=perm)

        new_token = create_playback_token(
            payload={
                "tenant_id": None,
                "video_id": video.id,
                "enrollment_id": enrollment.id,
                "user_id": request.user.id,
                "device_id": device_id,
                "session_id": session_id,
                "policy": policy,
            },
            ttl_seconds=ttl,
        )

        expires_at = int(time.time()) + ttl

        resp = PlaybackResponseSerializer({
            "token": new_token,
            "session_id": session_id,
            "expires_at": expires_at,
            "policy": policy,
            "play_url": self._public_play_url(video.id),
        })
        response = Response(resp.data, status=200)
        self._set_signed_cookies(response, video_id=video.id, expires_at=expires_at)
        return response

    @action(
        detail=True,
        methods=["post"],
        url_path="heartbeat",
        permission_classes=[IsAuthenticated, IsStudent],
    )
    def heartbeat(self, request, pk=None):
        req = PlaybackHeartbeatRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        token = req.validated_data["token"]

        ok, payload, err = verify_playback_token(token)
        if not ok:
            return Response({"detail": err}, status=401)

        if int(payload.get("user_id") or 0) != int(request.user.id):
            return Response({"detail": "user_mismatch"}, status=401)

        session_id = str(payload.get("session_id") or "")
        ttl = int(getattr(settings, "VIDEO_PLAYBACK_TTL_SECONDS", 600))

        ok_hb = heartbeat_session(user_id=request.user.id, session_id=session_id, ttl_seconds=ttl)
        if not ok_hb:
            return Response({"detail": "session_not_active"}, status=409)

        return Response({"status": "ok"}, status=200)

    @action(
        detail=True,
        methods=["post"],
        url_path="end",
        permission_classes=[IsAuthenticated, IsStudent],
    )
    def end(self, request, pk=None):
        req = PlaybackEndRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        token = req.validated_data["token"]

        ok, payload, err = verify_playback_token(token)
        if not ok:
            return Response({"detail": err}, status=401)

        if int(payload.get("user_id") or 0) != int(request.user.id):
            return Response({"detail": "user_mismatch"}, status=401)

        session_id = str(payload.get("session_id") or "")
        end_session(user_id=request.user.id, session_id=session_id)

        VideoPlaybackSession.objects.filter(session_id=session_id).update(
            status=VideoPlaybackSession.Status.ENDED,
            ended_at=timezone.now(),
        )

        response = Response({"status": "ended"}, status=200)

        path_prefix = self._hls_path_prefix_for_video(int(pk))
        opts = default_cookie_options(path_prefix=path_prefix)

        response.delete_cookie("CloudFront-Policy", domain=opts.get("domain"), path=opts.get("path"))
        response.delete_cookie("CloudFront-Signature", domain=opts.get("domain"), path=opts.get("path"))
        response.delete_cookie("CloudFront-Key-Pair-Id", domain=opts.get("domain"), path=opts.get("path"))
        return response
    
    #1) 학생용 “READY 목록 + 잠금정보” (GET)
    @action(
        detail=False,
        methods=["get"],
        url_path="student",
        permission_classes=[IsAuthenticated, IsStudent],
    )
    def student_list(self, request):
        """
        학생용 목록
        - READY만 노출
        - 각 video마다 can_play / reason 제공 (잠금 UI용)
        query:
          - session (필수): session id
        """
        session_id = request.query_params.get("session")
        if not session_id:
            return Response({"detail": "session is required"}, status=400)

        student = self._get_student_for_user(request)
        if student is None:
            return Response({"detail": "student_profile_not_linked"}, status=403)

        # READY만
        qs = Video.objects.select_related("session", "session__lecture").filter(
            session_id=session_id,
            status=Video.Status.READY,
        ).order_by("order", "id")

        # video -> lecture 역추적
        # (같은 session이면 lecture는 동일)
        session = Session.objects.select_related("lecture").get(id=session_id)
        lecture = session.lecture

        enrollment = Enrollment.objects.filter(
            student=student,
            lecture=lecture,
            status="ACTIVE",
        ).first()

        # 수강 자체가 없으면 전체 잠금
        if not enrollment:
            data = []
            for v in qs:
                d = VideoSerializer(v).data
                d["can_play"] = False
                d["reason"] = "not_enrolled"
                data.append(d)
            return Response(data)

        # session access 없는 경우도 잠금
        has_session_access = SessionEnrollment.objects.filter(
            session=session,
            enrollment=enrollment,
        ).exists()

        data = []
        for v in qs:
            d = VideoSerializer(v).data

            if not has_session_access:
                d["can_play"] = False
                d["reason"] = "no_session_access"
                data.append(d)
                continue

            ok, reason = self._check_access(video=v, enrollment=enrollment)
            d["can_play"] = bool(ok)
            d["reason"] = reason if not ok else None
            data.append(d)

        return Response(data)

    #2) 학생용 /play/facade/ (POST) — 토큰 + 쿠키 + play_url
    @action(
        detail=True,
        methods=["post"],
        url_path="play/facade",
        permission_classes=[IsAuthenticated, IsStudent],
    )
    def play_facade(self, request, pk=None):
        """
        학생용 Facade 재생 시작
        - 프론트는 video_id(pk) + device_id만 전달
        - 서버가 enrollment 역추적 + 권한검사 + token/cookie/play_url 발급
        """
        video = self.get_object()

        # 요청 검증
        from .serializers import PlaybackStartFacadeRequestSerializer
        req = PlaybackStartFacadeRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        device_id = req.validated_data["device_id"]

        student = self._get_student_for_user(request)
        if student is None:
            return Response({"detail": "student_profile_not_linked"}, status=403)

        lecture = video.session.lecture
        enrollment = Enrollment.objects.filter(
            student=student,
            lecture=lecture,
            status="ACTIVE",
        ).first()
        if not enrollment:
            return Response({"detail": "enrollment_not_found"}, status=403)

        # session access (핵심)
        if not SessionEnrollment.objects.filter(
            session=video.session,
            enrollment=enrollment,
        ).exists():
            return Response({"detail": "no_session_access"}, status=403)

        # 기존 access 체크 (READY 포함, once/blocked 등)
        ok, reason = self._check_access(video=video, enrollment=enrollment)
        if not ok:
            return Response({"detail": reason}, status=403)

        perm = self._load_permission(video=video, enrollment=enrollment)
        policy = self._effective_policy(video=video, perm=perm)

        ttl = int(getattr(settings, "VIDEO_PLAYBACK_TTL_SECONDS", 600))
        max_sessions = int(policy["concurrency"]["max_sessions"])
        max_devices = int(policy["concurrency"]["max_devices"])

        ok_sess, sess, err = issue_session(
            user_id=request.user.id,
            device_id=device_id,
            ttl_seconds=ttl,
            max_sessions=max_sessions,
            max_devices=max_devices,
        )
        if not ok_sess:
            return Response({"detail": err}, status=409)

        session_id = str(sess["session_id"])
        expires_at = int(sess["expires_at"])

        VideoPlaybackSession.objects.create(
            video=video,
            enrollment=enrollment,
            session_id=session_id,
            device_id=device_id,
            status=VideoPlaybackSession.Status.ACTIVE,
        )

        token = create_playback_token(
            payload={
                "tenant_id": None,
                "video_id": video.id,
                "enrollment_id": enrollment.id,
                "user_id": request.user.id,
                "device_id": device_id,
                "session_id": session_id,
                "policy": policy,
            },
            ttl_seconds=ttl,
        )

        resp = PlaybackResponseSerializer({
            "token": token,
            "session_id": session_id,
            "expires_at": expires_at,
            "policy": policy,
            "play_url": self._public_play_url(video.id),
        })

        response = Response(resp.data, status=200)
        self._set_signed_cookies(response, video_id=video.id, expires_at=expires_at)
        return response



    # =======================================================
    # Events API (v1: audit-only)
    # =======================================================

    def _epoch_to_dt(self, epoch: int | None) -> datetime:
        if not epoch:
            return timezone.now()
        try:
            return datetime.fromtimestamp(int(epoch), tz=timezone.get_current_timezone())
        except Exception:
            return timezone.now()

    # --------------------------------------------------
    # 위반 판정 (PATCH 반영: seek 정책 기반 + legacy fallback)
    # --------------------------------------------------
    def _evaluate_violation(self, *, event_type: str, payload: dict, policy: dict) -> tuple[bool, str]:
        if event_type == VideoPlaybackEvent.EventType.SEEK_ATTEMPT:
            seek = policy.get("seek") or {}
            mode = seek.get("mode")
            grace = float(seek.get("grace_seconds", 0) or 0)

            # legacy fallback (seek 정책이 없는 구버전 프론트)
            if not mode:
                if not bool(policy.get("allow_seek", False)):
                    return True, "seek_not_allowed"
                return False, ""

            # mode 기반 판정
            if mode == "blocked":
                return True, "seek_blocked"

            if mode == "bounded_forward":
                to = payload.get("to")
                max_watched = payload.get("max_watched")
                try:
                    to = float(to)
                    max_watched = float(max_watched)
                except Exception:
                    # payload 부족하면 판단 불가 → 위반 처리 안 함
                    return False, ""

                # 뒤로 가기(to <= max_watched)는 허용
                # 앞으로 점프만 제한
                if to > max_watched + grace:
                    return True, "seek_forward_exceeded"

        if event_type == VideoPlaybackEvent.EventType.SPEED_CHANGE_ATTEMPT:
            pr = policy.get("playback_rate") or {}
            ui_control = bool(pr.get("ui_control", True))
            max_rate = float(pr.get("max", 1.0) or 1.0)

            to_rate = payload.get("to")
            try:
                to_rate = float(to_rate)
            except Exception:
                to_rate = None

            if not ui_control:
                return True, "speed_control_disabled"
            if to_rate is not None and to_rate > max_rate:
                return True, "speed_exceeds_max"

        return False, ""

    @action(
        detail=True,
        methods=["post"],
        url_path="events",
        permission_classes=[IsAuthenticated, IsStudent],
    )
    def events(self, request, pk=None):
        video = self.get_object()

        req = PlaybackEventBatchRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)

        token = req.validated_data["token"]
        events = req.validated_data["events"]

        ok, payload, err = verify_playback_token(token)
        if not ok:
            return Response({"detail": err}, status=401)

        if int(payload.get("video_id") or 0) != int(video.id):
            return Response({"detail": "video_mismatch"}, status=401)
        if int(payload.get("user_id") or 0) != int(request.user.id):
            return Response({"detail": "user_mismatch"}, status=401)

        enrollment_id = int(payload.get("enrollment_id") or 0)
        session_id = str(payload.get("session_id") or "")

        if not is_session_active(user_id=request.user.id, session_id=session_id):
            return Response({"detail": "session_not_active"}, status=409)

        student = self._get_student_for_user(request)
        if student is None:
            return Response({"detail": "student_profile_not_linked"}, status=403)

        try:
            enrollment = Enrollment.objects.get(id=enrollment_id, student=student, status="ACTIVE")
        except Enrollment.DoesNotExist:
            return Response({"detail": "enrollment_not_found"}, status=404)

        ok_access, reason = self._check_access(video=video, enrollment=enrollment)
        if not ok_access:
            return Response({"detail": reason}, status=403)

        perm = self._load_permission(video=video, enrollment=enrollment)
        policy = self._effective_policy(video=video, perm=perm)

        rows: list[VideoPlaybackEvent] = []
        for ev in events:
            ev_type = ev.get("type")
            ev_payload = ev.get("payload") or {}
            occurred_at_epoch = ev.get("occurred_at")

            violated, reason = self._evaluate_violation(
                event_type=ev_type,
                payload=ev_payload,
                policy=policy,
            )

            rows.append(
                VideoPlaybackEvent(
                    video=video,
                    enrollment=enrollment,
                    session_id=session_id,
                    user_id=request.user.id,
                    event_type=ev_type,
                    event_payload=ev_payload,
                    policy_snapshot=policy,
                    violated=violated,
                    violation_reason=reason,
                    occurred_at=self._epoch_to_dt(occurred_at_epoch),
                )
            )

        if rows:
            VideoPlaybackEvent.objects.bulk_create(rows, batch_size=500)

        resp = PlaybackEventBatchResponseSerializer({"stored": len(rows)})
        return Response(resp.data, status=200)




class VideoPermissionViewSet(ModelViewSet):
    queryset = VideoPermission.objects.all()
    serializer_class = VideoPermissionSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["video", "enrollment"]
    permission_classes = [IsAuthenticated]


class VideoProgressViewSet(ModelViewSet):
    queryset = VideoProgress.objects.all()
    serializer_class = VideoProgressSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["video", "enrollment"]
    permission_classes = [IsAuthenticated]


# =======================================================
# Step 3: HLS Serving (v1) – Django Static Serve
# URL 계약: /hls/videos/...
# =======================================================

class HLSMediaServeView(View):
    """
    /hls/videos/{video_id}/master.m3u8
    /hls/videos/{video_id}/v1/index.m3u8
    /hls/videos/{video_id}/v1/seg_000.ts
    """

    ALLOWED_EXTENSIONS = {".m3u8", ".ts"}

    def get(self, request, video_id: int, path: str):
        base_dir = (
            Path(settings.BASE_DIR)
            / "storage"
            / "media"
            / "hls"
            / "videos"
            / str(video_id)
        )

        target = (base_dir / path).resolve()

        try:
            target.relative_to(base_dir.resolve())
        except ValueError:
            raise Http404("Invalid path")

        if target.suffix not in self.ALLOWED_EXTENSIONS:
            raise Http404("Invalid file type")

        if not target.exists() or not target.is_file():
            raise Http404("File not found")

        content_type, _ = mimetypes.guess_type(str(target))
        return FileResponse(
            open(target, "rb"),
            content_type=content_type or "application/octet-stream",
        )


# =======================================================
# Playback Session API (Student Facade)
# =======================================================
class PlaybackSessionView(APIView):
    permission_classes = [IsAuthenticated, IsStudent]

    def post(self, request):
        student = request.user.student_profile
        video_id = request.data.get("video_id")

        if not video_id:
            raise ValidationError("video_id is required")

        video = get_object_or_404(Video, id=video_id)
        lecture = video.session.lecture

        enrollment = Enrollment.objects.filter(
            student=student,
            lecture=lecture,
            status="ACTIVE",
        ).first()

        if not enrollment:
            raise PermissionDenied("Not enrolled in this lecture")

        # ✅ 핵심: 세션 접근 권한
        if not SessionEnrollment.objects.filter(
            session=video.session,
            enrollment=enrollment,
        ).exists():
            raise PermissionDenied("No session access")

        result = create_playback_session(
            user=request.user,
            video_id=video.id,
            enrollment_id=enrollment.id,
        )

        if not result.get("ok"):
            return Response(
                {"detail": result["error"]},
                status=status.HTTP_409_CONFLICT,
            )

        return Response(
            PlaybackSessionSerializer(result).data,
            status=status.HTTP_201_CREATED,
        )





class VideoProcessingCompleteView(APIView):
    """
    ⚠️ 내부 전용
    worker → API ACK 용
    """
    permission_classes = [AllowAny]

    def post(self, request, video_id: int):
        token = request.headers.get("X-Worker-Token")
        if token != settings.INTERNAL_WORKER_TOKEN:
            return Response(status=status.HTTP_403_FORBIDDEN)

        # ❌ DB 수정 금지
        # worker가 single source of truth

        return Response({"status": "ack"}, status=200)




#apps/support/media/serializers.py 맨 아래 아무데나 추가:

class PlaybackStartFacadeRequestSerializer(serializers.Serializer):
    device_id = serializers.CharField(max_length=128)