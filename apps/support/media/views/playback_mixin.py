import time

from django.conf import settings
from rest_framework.response import Response

from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.lectures.models import Session

from ..models import Video, VideoPermission, VideoProgress
from ..serializers import VideoSerializer
from ..drm import create_playback_token, verify_playback_token
from ..cdn.cloudfront import build_signed_cookies_for_path, default_cookie_options


class VideoPlaybackMixin:
    """
    재생 권한 / 정책 / 공통 로직

    정책 정의:
    - free    : 항상 무제한
    - once    : 1회차에만 정책 적용, 완료 후 free로 승격됨
    - blocked : 항상 차단
    """

    def _get_student_for_user(self, request):
        return getattr(request.user, "student_profile", None)

    # ==================================================
    # 접근 제어 (Access Control)
    # ==================================================
    def _check_access(self, *, video, enrollment):
        """
        접근 가능 여부만 판단한다.
        once는 접근을 차단하지 않는다.
        """
        if video.status != video.Status.READY:
            return False, "video_not_ready"

        if not SessionEnrollment.objects.filter(
            session=video.session,
            enrollment=enrollment,
        ).exists():
            return False, "no_session_access"

        perm = VideoPermission.objects.filter(
            video=video,
            enrollment=enrollment,
        ).first()

        rule = perm.rule if perm else "free"

        if rule == "blocked":
            return False, "blocked"

        # free / once 모두 접근 허용
        return True, None

    # ==================================================
    # Permission Loader
    # ==================================================
    def _load_permission(self, *, video, enrollment):
        return VideoPermission.objects.filter(
            video=video,
            enrollment=enrollment,
        ).first()

    # ==================================================
    # Playback Policy
    # ==================================================
    def _effective_policy(self, *, video, perm):
        """
        실제 재생 제약 정책 계산
        - once : 완료 전까지 탐색 제한
        - 완료 후에는 free와 동일
        """
        allow_seek = bool(video.allow_skip)
        max_rate = float(video.max_speed or 1.0)
        watermark_enabled = bool(video.show_watermark)
        ui_speed_control = True

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

            if perm.rule == "once":
                completed = VideoProgress.objects.filter(
                    video=video,
                    enrollment=perm.enrollment,
                    completed=True,
                ).exists()

                if not completed:
                    seek_policy = {
                        "mode": "bounded_forward",
                        "forward_limit": "max_watched",
                        "grace_seconds": 3,
                    }

            if perm.block_seek:
                allow_seek = False
                seek_policy = {"mode": "blocked"}

            if perm.block_speed_control:
                ui_speed_control = False
                max_rate = 1.0

        return {
            "allow_seek": allow_seek,
            "seek": seek_policy,
            "playback_rate": {
                "max": max_rate,
                "ui_control": ui_speed_control,
            },
            "watermark": {
                "enabled": watermark_enabled,
                "mode": "overlay",
                "fields": ["user_id"],
            },
            "concurrency": {
                "max_sessions": int(getattr(settings, "VIDEO_MAX_SESSIONS", 9999)),
                "max_devices": int(getattr(settings, "VIDEO_MAX_DEVICES", 9999)),
            },
        }

    # ==================================================
    # HLS / CDN
    # ==================================================
    def _hls_path_prefix_for_video(self, video_id: int) -> str:
        return f"/hls/videos/{video_id}/"

    def _public_play_url(self, video_id: int) -> str:
        cdn_base = settings.CDN_HLS_BASE_URL.rstrip("/")
        return f"{cdn_base}/media/hls/videos/{video_id}/master.m3u8"

    def _set_signed_cookies(self, response: Response, *, video_id: int, expires_at: int):
        path_prefix = self._hls_path_prefix_for_video(video_id)
        cookies = build_signed_cookies_for_path(path_prefix, expires_at)
        opts = default_cookie_options(path_prefix)

        max_age = max(0, expires_at - int(time.time()))
        for k, v in cookies.items():
            response.set_cookie(k, v, max_age=max_age, **opts)

    # ==================================================
    # 학생 영상 목록 (재생 가능 여부 판단)
    # ==================================================
    def _student_list_impl(self, request):
        session_id = request.query_params.get("session")
        if not session_id:
            return Response({"detail": "session is required"}, status=400)

        student = self._get_student_for_user(request)
        if student is None:
            return Response({"detail": "student_profile_not_linked"}, status=403)

        qs = Video.objects.filter(
            session_id=session_id,
            status=Video.Status.READY,
        ).order_by("order", "id")

        session = Session.objects.select_related("lecture").get(id=session_id)
        lecture = session.lecture

        enrollment = Enrollment.objects.filter(
            student=student,
            lecture=lecture,
            status="ACTIVE",
        ).first()

        data = []
        for v in qs:
            d = VideoSerializer(v).data

            if not enrollment:
                d["can_play"] = False
                d["reason"] = "not_enrolled"
                data.append(d)
                continue

            if not SessionEnrollment.objects.filter(
                session=session,
                enrollment=enrollment,
            ).exists():
                d["can_play"] = False
                d["reason"] = "no_session_access"
                data.append(d)
                continue

            ok, reason = self._check_access(video=v, enrollment=enrollment)
            d["can_play"] = bool(ok)
            d["reason"] = reason if not ok else None
            data.append(d)

        return Response(data)
