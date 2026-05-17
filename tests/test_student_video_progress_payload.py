from apps.domains.student_app.media.views import (
    _safe_video_completed,
    _safe_video_position,
    _safe_video_progress,
)
from apps.domains.student_app.media.serializers import StudentVideoPlaybackSerializer
from scripts.post_deploy_smoke.video_playback_chain import _resolve_hls_relative


def test_safe_video_progress_accepts_string_percent():
    assert _safe_video_progress("50") == 0.5
    assert _safe_video_progress("0.25") == 0.25


def test_safe_video_progress_clamps_invalid_and_out_of_range_values():
    assert _safe_video_progress("bad") == 0.0
    assert _safe_video_progress("-3") == 0.0
    assert _safe_video_progress("150") == 1.0


def test_safe_video_completed_parses_false_like_strings():
    for value in [False, None, 0, "0", "false", "False", "no", "off", ""]:
        assert _safe_video_completed(value) is False
    for value in [True, 1, "1", "true", "yes", "done"]:
        assert _safe_video_completed(value) is True


def test_safe_video_position_accepts_numeric_strings_and_rejects_bad_values():
    assert _safe_video_position("12.8") == 12
    assert _safe_video_position("-7") == 0
    assert _safe_video_position("bad") == 0


def test_student_video_playback_serializer_allows_public_video_without_session():
    payload = {
        "video": {
            "id": 284,
            "session_id": None,
            "title": "public",
            "status": "READY",
            "thumbnail_url": None,
            "duration": 120,
            "allow_skip": False,
            "max_speed": 1.0,
            "show_watermark": True,
            "effective_rule": "free",
            "access_mode": None,
        },
        "hls_url": "https://cdn.example.test/master.m3u8",
        "mp4_url": None,
        "play_url": "https://cdn.example.test/master.m3u8",
        "policy": {
            "allow_seek": True,
            "monitoring_enabled": False,
        },
    }

    data = StudentVideoPlaybackSerializer(payload).data

    assert data["video"]["session_id"] is None


def test_video_smoke_resolves_hls_relative_paths_from_manifest_directory():
    master = "https://cdn.example.test/tenants/1/video/hls/284/master.m3u8?exp=1&sig=x"
    variant = _resolve_hls_relative(master, "v2/index.m3u8?exp=2&sig=y")
    segment = _resolve_hls_relative(variant, "seg-00001.ts?exp=3&sig=z")

    assert variant == "https://cdn.example.test/tenants/1/video/hls/284/v2/index.m3u8?exp=2&sig=y"
    assert segment == "https://cdn.example.test/tenants/1/video/hls/284/v2/seg-00001.ts?exp=3&sig=z"
