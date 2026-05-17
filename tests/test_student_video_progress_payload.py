from apps.domains.student_app.media.views import (
    _safe_video_completed,
    _safe_video_position,
    _safe_video_progress,
)


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
