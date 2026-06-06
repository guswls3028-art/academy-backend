from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.domains.messaging.policy import MessagingPolicyError
from apps.domains.messaging.sqs_queue import MessagingSQSQueue
from apps.domains.messaging.services import enqueue_sms
from apps.worker.messaging_worker.sqs_main import _normalize_worker_tenants, _video_encoding_block_reason


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
        with pytest.raises(MessagingPolicyError) as exc:
            enqueue_sms(
                tenant_id=1,
                to="01012345678",
                text="영상 인코딩 완료",
                message_mode="sms",
                event_type="video_encoding_complete",
            )

    assert exc.value.reason == "sms_disabled"


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


def test_business_key_includes_source_tenant_for_owner_proxy_sends() -> None:
    fake_client = _FakeQueueClient()
    with patch("apps.domains.messaging.sqs_queue.get_queue_client", return_value=fake_client):
        queue = MessagingSQSQueue()
        common = {
            "tenant_id": 1,
            "to": "01012345678",
            "text": "출결 알림",
            "message_mode": "alimtalk",
            "template_id": "KA01ATTEND",
            "event_type": "check_in_complete",
            "target_type": "student",
            "target_id": 10,
            "occurrence_key": "check_in_complete:session:10",
        }
        assert queue.enqueue(**common, source_tenant_id=2)
        assert queue.enqueue(**common, source_tenant_id=3)

    keys = [m["business_idempotency_key"] for m in fake_client.messages]
    assert len(keys) == 2
    assert keys[0] != keys[1]


def test_worker_normalizes_raw_tenant_payload_to_common_owner() -> None:
    tenant_id, source_tenant_id = _normalize_worker_tenants(
        3,
        None,
        owner_tenant_id=1,
    )

    assert tenant_id == 1
    assert source_tenant_id == 3


def test_worker_preserves_existing_source_when_payload_is_already_owner() -> None:
    tenant_id, source_tenant_id = _normalize_worker_tenants(
        1,
        3,
        owner_tenant_id=1,
    )

    assert tenant_id == 1
    assert source_tenant_id == 3
