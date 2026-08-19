from __future__ import annotations

from unittest.mock import Mock, patch

from apps.domains.messaging.ppurio_client import send_ppurio_alimtalk, send_ppurio_sms


def _response(status_code: int, payload: dict) -> Mock:
    resp = Mock()
    resp.status_code = status_code
    resp.text = "x"
    resp.json.return_value = payload
    return resp


@patch("apps.support.messaging.ppurio_client.requests.post")
def test_ppurio_sms_is_hard_blocked_before_provider(mock_post: Mock) -> None:
    result = send_ppurio_sms(
        "01012345678",
        "hello",
        "01011112222",
        api_key="configured",
        account="configured",
    )

    assert result == {
        "status": "error",
        "reason": "sms_disabled",
        "provider_called": False,
    }
    mock_post.assert_not_called()


@patch("apps.support.messaging.ppurio_client.requests.post")
def test_ppurio_alimtalk_boundary_failure_stops_before_message_post(mock_post: Mock) -> None:
    mock_post.return_value = _response(200, {"token": "access-token"})
    boundary = Mock(return_value=False)

    result = send_ppurio_alimtalk(
        "01012345678",
        "0212345678",
        "@academy",
        "TEMPLATE",
        api_key="key",
        account="tenant",
        before_provider_call=boundary,
    )

    boundary.assert_called_once_with()
    assert mock_post.call_count == 1  # access token only; no /v1/kakao request
    assert result["reason"] == "provider_boundary_claim_failed"
    assert result["provider_called"] is False


@patch("apps.support.messaging.ppurio_client.requests.post")
def test_ppurio_alimtalk_marks_actual_message_post_as_provider_called(mock_post: Mock) -> None:
    mock_post.side_effect = [
        _response(200, {"token": "access-token"}),
        _response(201, {"messageKey": "message-1"}),
    ]
    boundary = Mock(return_value=True)

    result = send_ppurio_alimtalk(
        "01012345678",
        "0212345678",
        "@academy",
        "TEMPLATE",
        api_key="key",
        account="tenant",
        before_provider_call=boundary,
    )

    boundary.assert_called_once_with()
    assert mock_post.call_count == 2
    assert result["status"] == "ok"
    assert result["provider_called"] is True


@patch("apps.support.messaging.ppurio_client.requests.post")
def test_ppurio_alimtalk_explicit_rejection_is_definite_and_non_retryable(mock_post: Mock) -> None:
    mock_post.side_effect = [
        _response(200, {"token": "access-token"}),
        _response(400, {"code": "E400", "description": "invalid template"}),
    ]

    result = send_ppurio_alimtalk(
        "01012345678",
        "0212345678",
        "@academy",
        "BAD-TEMPLATE",
        api_key="key",
        account="tenant",
    )

    assert result["status"] == "error"
    assert result["provider_called"] is True
    assert result["provider_outcome"] == "rejected"
    assert result["definitely_not_accepted"] is True
    assert result["provider_retryable"] is False


@patch("apps.support.messaging.ppurio_client.requests.post")
def test_ppurio_alimtalk_server_error_is_ambiguous_and_not_retryable(mock_post: Mock) -> None:
    mock_post.side_effect = [
        _response(200, {"token": "access-token"}),
        _response(503, {"code": "E503", "description": "gateway timeout"}),
    ]

    result = send_ppurio_alimtalk(
        "01012345678",
        "0212345678",
        "@academy",
        "TEMPLATE",
        api_key="key",
        account="tenant",
    )

    assert result["provider_called"] is True
    assert result["provider_outcome"] == "unknown"
    assert result["definitely_not_accepted"] is False
    assert "provider_retryable" not in result
