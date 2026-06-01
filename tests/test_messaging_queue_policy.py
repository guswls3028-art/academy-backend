from types import SimpleNamespace
from unittest.mock import patch

from apps.domains.messaging.sqs_queue import MessagingSQSQueue
from apps.domains.messaging.services import enqueue_sms
from apps.worker.messaging_worker.sqs_main import _video_encoding_block_reason


class _FakeQueueClient:
    def __init__(self):
        self.messages = []

    def send_message(self, *, queue_name: str, message: dict) -> bool:
        self.messages.append(dict(message))
        return True


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


def test_manual_enqueue_without_occurrence_key_gets_unique_business_key() -> None:
    fake_client = _FakeQueueClient()
    with (
        patch("apps.domains.messaging.sqs_queue.get_queue_client", return_value=fake_client),
        patch(
            "apps.domains.messaging.sqs_queue.uuid4",
            side_effect=[SimpleNamespace(hex="first"), SimpleNamespace(hex="second")],
        ),
    ):
        queue = MessagingSQSQueue()
        assert queue.enqueue(
            tenant_id=1,
            to="01012345678",
            text="첫 번째",
            message_mode="alimtalk",
            event_type="manual_send",
            target_type="student",
            target_id=10,
        )
        assert queue.enqueue(
            tenant_id=1,
            to="01012345678",
            text="두 번째",
            message_mode="alimtalk",
            event_type="manual_send",
            target_type="student",
            target_id=10,
        )

    keys = [m["business_idempotency_key"] for m in fake_client.messages]
    assert len(keys) == 2
    assert keys[0] != keys[1]
