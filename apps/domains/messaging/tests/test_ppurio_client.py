from __future__ import annotations

from unittest.mock import Mock, patch

from apps.domains.messaging.ppurio_client import send_ppurio_sms


def _response(status_code: int, payload: dict) -> Mock:
    resp = Mock()
    resp.status_code = status_code
    resp.text = "x"
    resp.json.return_value = payload
    return resp


def test_ppurio_sms_missing_credentials_is_non_retryable_configuration_failure() -> None:
    result = send_ppurio_sms("01012345678", "hello", "01011112222", api_key="", account="")

    assert result == {"status": "skipped", "reason": "ppurio_not_configured"}


@patch("apps.support.messaging.ppurio_client.requests.post")
def test_ppurio_sms_token_rejected_is_reported_as_permanent_failure(mock_post: Mock) -> None:
    mock_post.return_value = _response(401, {"code": "Unauthorized", "description": "bad credentials"})

    result = send_ppurio_sms(
        "01012345678",
        "hello",
        "01011112222",
        api_key="bad",
        account="tenant",
    )

    assert result == {"status": "error", "reason": "ppurio_token_rejected"}


@patch("apps.support.messaging.ppurio_client.requests.post")
def test_ppurio_sms_token_server_error_remains_retryable(mock_post: Mock) -> None:
    mock_post.return_value = _response(500, {"code": "E500", "description": "server down"})

    result = send_ppurio_sms(
        "01012345678",
        "hello",
        "01011112222",
        api_key="ok",
        account="tenant",
    )

    assert result == {"status": "error", "reason": "ppurio_token_unavailable"}
