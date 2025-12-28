# apps/support/media/views/playback_mixin.py

import time
from datetime import datetime

from django.conf import settings
from django.utils import timezone
from rest_framework.response import Response

from apps.domains.enrollment.models import Enrollment, SessionEnrollment

from ..models import VideoPermission, VideoProgress, VideoPlaybackSession
from ..drm import create_playback_token, verify_playback_token
from ..services.playback_session import (
    issue_session,
    heartbeat_session,
    end_session,
    is_session_active,
)
from ..cdn.cloudfront import build_signed_cookies_for_path, default_cookie_options


class VideoPlaybackMixin:
    """
    재생 권한 / 정책 / 공통 로직
    """

    def _get_student_for_user(self, request):
        return getattr(request.user, "student_profile", None)

    def _check_access(self, *, video, enrollment):
        if video.status != video.Status.READY:
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

    def _load_permission(self, *, video, enrollment):
        return VideoPermission.objects.filter(video=video, enrollment=enrollment).first()

    def _effective_policy(self, *, video, perm):
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
                seek_policy = {
                    "mode": "bounded_forward",
                    "forward_limit": "max_watched",
                    "grace_seconds": 3,
                }

            if getattr(perm, "block_seek", False):
                allow_seek = False
                seek_policy = {"mode": "blocked"}

            if getattr(perm, "block_speed_control", False):
                ui_speed_control = False
                max_rate = 1.0

        return {
            "allow_seek": allow_seek,
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
        cdn_base = settings.CDN_HLS_BASE_URL.rstrip("/")
        return f"{cdn_base}/media/hls/videos/{video_id}/master.m3u8"

    def _set_signed_cookies(self, response: Response, *, video_id: int, expires_at: int):
        path_prefix = self._hls_path_prefix_for_video(video_id)
        cookies = build_signed_cookies_for_path(path_prefix, expires_at)
        opts = default_cookie_options(path_prefix)

        max_age = max(0, expires_at - int(time.time()))
        for k, v in cookies.items():
            response.set_cookie(k, v, max_age=max_age, **opts)
