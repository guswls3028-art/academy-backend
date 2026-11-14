from unittest.mock import patch

import pytest

from scripts.post_deploy_smoke.video_playback_chain import (
    API_URL,
    MEDIA_ORIGIN,
    SmokeFail,
    assert_segment,
    assert_variant_chain,
    fetch_master,
    fetch_play_url,
    find_first_video,
)


def test_cdn_chain_requires_browser_origin_at_each_hop_and_a_readable_byte_range() -> None:
    master = "https://cdn.example/master.m3u8?sig=synthetic&exp=123"
    variant = "https://cdn.example/v1/index.m3u8?sig=synthetic&exp=123"
    segment = "https://cdn.example/v1/index0.ts?sig=synthetic&exp=123"
    cors = {"access-control-allow-origin": MEDIA_ORIGIN}
    responses = [
        (200, f"#EXTM3U\n{variant}".encode(), cors),
        (200, f"#EXTM3U\n{segment}".encode(), cors),
        (206, b"x" * 1024, {**cors, "Content-Range": "bytes 0-1023/8200",
                            "Access-Control-Expose-Headers": "Accept-Ranges, Content-Range"}),
    ]
    with patch("scripts.post_deploy_smoke.video_playback_chain._req", side_effect=responses) as request:
        fetch_master(master)
        playlist, url = assert_variant_chain([variant])
        assert_segment(playlist, url)

    assert [item.kwargs["headers"] for item in request.call_args_list] == [
        {"Origin": MEDIA_ORIGIN}, {"Origin": MEDIA_ORIGIN},
        {"Origin": MEDIA_ORIGIN, "Range": "bytes=0-1023"},
    ]


@pytest.mark.parametrize("allow_origin", [None, "https://wrong.example"])
def test_successful_http_status_does_not_hide_missing_browser_access(allow_origin, capsys) -> None:
    headers = {"Access-Control-Allow-Origin": allow_origin} if allow_origin else {}
    with (
        patch("scripts.post_deploy_smoke.video_playback_chain._req", return_value=(200, b"#EXTM3U", headers)),
        pytest.raises(SmokeFail),
    ):
        fetch_master("https://cdn.example/master.m3u8?sig=do-not-log")
    error = capsys.readouterr().err
    assert "does not allow the browser origin" in error
    assert "do-not-log" not in error


def test_fetch_play_url_uses_explicit_post_bootstrap() -> None:
    with patch(
        "scripts.post_deploy_smoke.video_playback_chain._post_json",
        return_value=(200, {"play_url": "https://cdn.example/master.m3u8"}),
    ) as post_json:
        result = fetch_play_url("student-token", 123, 456)

    assert result == "https://cdn.example/master.m3u8"
    post_json.assert_called_once_with(
        f"{API_URL}/api/v1/student/video/videos/123/playback/?enrollment=456",
        None,
        headers={"Authorization": "Bearer student-token"},
    )


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
            "scripts.post_deploy_smoke.video_playback_chain.STUDENT_USER",
            "secret-student-username",
        ),
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

    error = capsys.readouterr().err
    assert "checked sessions=[158, 160]" in error
    assert "secret-student-username" not in error


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
