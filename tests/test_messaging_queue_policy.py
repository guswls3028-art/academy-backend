from unittest.mock import patch

from apps.domains.messaging.services import enqueue_sms
from apps.worker.messaging_worker.sqs_main import _video_encoding_block_reason


def test_video_encoding_complete_sms_is_blocked_even_when_sms_allowed() -> None:
    with (
        patch("apps.domains.messaging.policy.is_messaging_disabled", return_value=False),
        patch("apps.domains.messaging.policy.is_messaging_restricted", return_value=False),
        patch("apps.domains.messaging.policy.check_recipient_allowed", return_value=True),
        patch("apps.domains.messaging.policy.can_send_sms", return_value=True),
    ):
        result = enqueue_sms(
            tenant_id=1,
            to="01012345678",
            text="영상 인코딩 완료",
            message_mode="sms",
            event_type="video_encoding_complete",
        )

    assert result is False


def test_worker_blocks_video_encoding_complete_sms_and_missing_template() -> None:
    assert (
        _video_encoding_block_reason(
            event_type="video_encoding_complete",
            message_mode="sms",
            template_id="KA01VIDEO",
        )
        == "video_encoding_complete_sms_blocked"
    )
    assert (
        _video_encoding_block_reason(
            event_type="video_encoding_complete",
            message_mode="alimtalk",
            template_id="",
        )
        == "video_encoding_complete_template_required"
    )
    assert (
        _video_encoding_block_reason(
            event_type="video_encoding_complete",
            message_mode="alimtalk",
            template_id="KA01VIDEO",
        )
        == ""
    )
