"""Canonical effective playback policy shared by student and legacy playback APIs."""

from __future__ import annotations

from apps.domains.video.models import AccessMode
from apps.domains.video.policy import video_forward_skip_budget


def build_effective_playback_policy(
    *,
    video,
    access_mode: AccessMode | str,
    permission=None,
    progress=None,
) -> dict:
    mode = access_mode if isinstance(access_mode, AccessMode) else AccessMode(access_mode)
    allow_seek = bool(video.allow_skip)
    max_rate = float(video.max_speed or 1.0)
    watermark_enabled = bool(video.show_watermark)
    ui_speed_control = True
    seek_policy = {
        "mode": "free" if allow_seek else "blocked",
        "forward_limit": None,
        "grace_seconds": 3,
    }

    if permission:
        if permission.allow_skip_override is not None:
            allow_seek = bool(permission.allow_skip_override)
        if permission.max_speed_override is not None:
            max_rate = float(permission.max_speed_override)
        if permission.show_watermark_override is not None:
            watermark_enabled = bool(permission.show_watermark_override)
        if permission.block_seek:
            allow_seek = False
            seek_policy = {"mode": "blocked"}
        if permission.block_speed_control:
            ui_speed_control = False
            max_rate = 1.0

    if mode == AccessMode.BLOCKED:
        allow_seek = False
        seek_policy = {"mode": "blocked"}
    elif mode == AccessMode.PROCTORED_CLASS:
        if not permission or not permission.block_seek:
            budget = video_forward_skip_budget(
                duration=video.duration,
                used_seconds=getattr(progress, "forward_skip_seconds_used", 0),
            )
            allow_seek = True
            seek_policy = {
                "mode": "budgeted_forward",
                "forward_limit": "budget",
                "grace_seconds": 3,
                **budget,
            }

        if not permission or permission.max_speed_override is None:
            video_max = float(video.max_speed or 1.0)
            max_rate = video_max if video_max > 1.0 else 1.0
            ui_speed_control = True
        if not permission or permission.show_watermark_override is None:
            watermark_enabled = True
    elif mode == AccessMode.FREE_REVIEW:
        if not allow_seek and (not permission or not permission.block_seek):
            budget = video_forward_skip_budget(
                duration=video.duration,
                used_seconds=getattr(progress, "forward_skip_seconds_used", 0),
            )
            allow_seek = True
            seek_policy = {
                "mode": "budgeted_forward",
                "forward_limit": "budget",
                "grace_seconds": 3,
                **budget,
            }
        else:
            seek_policy = {
                "mode": "free" if allow_seek else "blocked",
                "forward_limit": None,
                "grace_seconds": 3,
            }

    return {
        "access_mode": mode.value,
        "monitoring_enabled": mode == AccessMode.PROCTORED_CLASS,
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
    }
