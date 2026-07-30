from unittest.mock import patch

import pytest

from scripts.post_deploy_smoke.video_playback_chain import API_URL, SmokeFail, find_first_video


def test_find_first_video_skips_enrolled_sessions_without_videos() -> None:
    video_me = {
        "lectures": [
            {
                "id": 96,
                "enrollment_id": 10,
                "sessions": [{"id": 158}, {"id": 159}],
            }
        ]
    }
    with patch(
        "scripts.post_deploy_smoke.video_playback_chain._get_json",
        side_effect=[
            (200, video_me),
            (200, {"items": []}),
            (200, {"items": [{"id": 700}]}),
        ],
    ) as get_json:
        result = find_first_video("token")

    assert result == (96, 159, 700)
    assert get_json.call_args_list[1].args[0] == (
        f"{API_URL}/api/v1/student/video/sessions/158/videos/?enrollment=10"
    )
    assert get_json.call_args_list[2].args[0] == (
        f"{API_URL}/api/v1/student/video/sessions/159/videos/?enrollment=10"
    )


def test_find_first_video_reports_all_empty_enrolled_sessions(capsys: pytest.CaptureFixture[str]) -> None:
    video_me = {
        "lectures": [
            {"id": 96, "enrollment_id": 10, "sessions": [{"id": 158}]},
            {"id": 97, "enrollment_id": 11, "sessions": [{"id": 160}]},
        ]
    }
    with (
        patch(
            "scripts.post_deploy_smoke.video_playback_chain._get_json",
            side_effect=[
                (200, video_me),
                (200, {"items": []}),
                (200, {"items": []}),
            ],
        ),
        pytest.raises(SmokeFail),
    ):
        find_first_video("token")

    assert "checked sessions=[158, 160]" in capsys.readouterr().err


def test_find_first_video_falls_back_to_public_session() -> None:
    video_me = {
        "public": {"lecture_id": 136, "session_id": 159},
        "lectures": [
            {"id": 96, "enrollment_id": 10, "sessions": [{"id": 158}]},
        ],
    }
    with patch(
        "scripts.post_deploy_smoke.video_playback_chain._get_json",
        side_effect=[
            (200, video_me),
            (200, {"items": []}),
            (200, {"items": [{"id": 284}]}),
        ],
    ) as get_json:
        result = find_first_video("token")

    assert result == (136, 159, 284)
    assert get_json.call_args_list[2].args[0] == (
        f"{API_URL}/api/v1/student/video/sessions/159/videos/"
    )
