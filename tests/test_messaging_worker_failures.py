from apps.worker.messaging_worker.sqs_main import _is_non_retryable_send_failure


def test_ppurio_credential_failures_are_non_retryable() -> None:
    assert _is_non_retryable_send_failure("ppurio_not_configured")
    assert _is_non_retryable_send_failure("ppurio_token_rejected")
    assert _is_non_retryable_send_failure("ppurio_token_client_error_401")
    assert _is_non_retryable_send_failure("ppurio_token_failed")
    assert _is_non_retryable_send_failure("invalid_sender_profile_format")


def test_ppurio_provider_unavailable_remains_retryable() -> None:
    assert not _is_non_retryable_send_failure("ppurio_token_unavailable")
