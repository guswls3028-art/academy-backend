from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from academy.adapters.queue.sqs.ai_queue import SQSAIQueueAdapter
from academy.adapters.queue.sqs.tools_queue import SQSToolsQueueAdapter
from academy.framework.workers import ai_sqs_worker as worker
from apps.support.ai.services.sqs_queue import AISQSQueue
from libs.queue.client import QueueUnavailableError, SQSQueueClient, _is_auth_error


def _client():
    client = SQSQueueClient.__new__(SQSQueueClient)
    client.region_name = "qa-region"
    client.sqs = MagicMock()
    client.sqs.get_queue_url.return_value = {"QueueUrl": "https://example.invalid/qa"}
    return client


def _denied(code, operation="ReceiveMessage"):
    return ClientError({"Error": {"Code": code, "Message": "synthetic denial"}}, operation)


@pytest.mark.parametrize("code", ["AccessDenied", "AccessDeniedException"])
def test_receive_denial_is_unavailable_not_empty(code):
    client = _client()
    error = _denied(code)
    client.sqs.receive_message.side_effect = error
    with pytest.raises(QueueUnavailableError) as raised:
        client.receive_message("qa-queue")
    assert raised.value.cause is error
    assert raised.value.__cause__ is error
    client.sqs.receive_message.assert_called_once_with(
        QueueUrl="https://example.invalid/qa", MaxNumberOfMessages=1,
        WaitTimeSeconds=20, MessageAttributeNames=["All"],
    )
    assert not _is_auth_error(error)


@pytest.mark.parametrize("code", [
    "InvalidClientTokenId", "UnrecognizedClientException",
    "SignatureDoesNotMatch", "InvalidSignatureException",
])
def test_existing_credential_errors_still_use_unavailable_path(code):
    client = _client()
    error = _denied(code)
    client.sqs.receive_message.side_effect = error
    with pytest.raises(QueueUnavailableError) as raised:
        client.receive_message("qa-queue")
    assert raised.value.cause is error
    assert _is_auth_error(error)


def test_normal_empty_queue_and_received_message_are_preserved():
    client = _client()
    message = {"Body": "synthetic", "ReceiptHandle": "qa-receipt"}
    client.sqs.receive_message.side_effect = [{"Messages": []}, {"Messages": [message]}]
    assert client.receive_message("qa-queue") is None
    assert client.receive_message("qa-queue") is message


def test_other_receive_errors_keep_the_existing_result():
    client = _client()
    client.sqs.receive_message.side_effect = _denied("AWS.SimpleQueueService.NonExistentQueue")
    assert client.receive_message("qa-queue") is None


@pytest.mark.parametrize("code", ["AccessDenied", "AccessDeniedException"])
def test_queue_url_denial_keeps_existing_lookup_and_receive_results(code):
    client = _client()
    error = _denied(code, operation="GetQueueUrl")
    client.sqs.get_queue_url.side_effect = error
    with pytest.raises(ClientError) as raised:
        client._get_queue_url("qa-queue")
    assert raised.value is error
    assert client.receive_message("qa-queue") is None
    client.sqs.receive_message.assert_not_called()


@pytest.mark.parametrize("code", ["AccessDenied", "AccessDeniedException"])
def test_receive_denial_does_not_change_visibility_delete_or_send(code):
    client = _client()
    error = _denied(code)
    client.sqs.receive_message.side_effect = error
    with pytest.raises(QueueUnavailableError):
        client.receive_message("qa-queue")
    assert client.change_message_visibility("qa-queue", "qa-receipt", 3600)
    assert client.delete_message("qa-queue", "qa-receipt")
    client.sqs.change_message_visibility.assert_called_once_with(
        QueueUrl="https://example.invalid/qa", ReceiptHandle="qa-receipt", VisibilityTimeout=3600,
    )
    client.sqs.delete_message.assert_called_once_with(
        QueueUrl="https://example.invalid/qa", ReceiptHandle="qa-receipt",
    )
    client.sqs.send_message.side_effect = error
    with pytest.raises(ClientError) as raised:
        client.send_message("qa-queue", {"synthetic": True})
    assert raised.value is error
    client.sqs.delete_message.side_effect = error
    client.sqs.change_message_visibility.side_effect = error
    assert not client.delete_message("qa-queue", "qa-receipt")
    assert not client.change_message_visibility("qa-queue", "qa-receipt", 3600)


@pytest.mark.parametrize("kind", ["ai", "tools"])
@pytest.mark.parametrize("code", ["AccessDenied", "AccessDeniedException"])
def test_actual_adapters_back_off_without_idle_scale_in_or_claim(kind, code):
    client = _client()
    implementation = AISQSQueue.__new__(AISQSQueue)
    implementation.queue_client = client
    implementation.queue_name_override = "qa-queue"
    queue = SQSAIQueueAdapter() if kind == "ai" else SQSToolsQueueAdapter("qa-queue")
    queue._impl = implementation
    polls = []

    def deny_receive(**kwargs):
        polls.append(kwargs)
        # Bound a regression on the old empty-queue fallback without an infinite loop.
        if len(polls) > 1:
            worker._shutdown = True
        raise _denied(code)

    def finish_backoff(seconds):
        assert seconds == 60
        worker._shutdown = True

    client.sqs.receive_message.side_effect = deny_receive
    with (
        patch.object(worker, "_shutdown", False),
        patch.object(worker, "IDLE_SCALE_IN_ENABLED", True),
        patch.object(worker, "IDLE_EMPTY_POLLS_BEFORE_SCALE_IN", 1),
        patch.object(worker, "_try_idle_scale_in", return_value=True) as scale_in,
        patch.object(worker, "prepare_ai_job") as claim,
        patch.object(worker, "SQSVisibilityExtender") as extender,
        patch.object(worker, "close_old_connections"),
        patch.object(worker, "_release_db_connections"),
        patch.object(worker.signal, "signal"),
        patch.object(worker.time, "sleep", side_effect=finish_backoff) as sleep,
        patch("apps.shared.utils.heartbeat.beat") as heartbeat,
    ):
        assert worker.run_ai_sqs_worker(queue=queue, worker_kind=kind) == 0
        sleep.assert_called_once_with(60)
        scale_in.assert_not_called()
        claim.assert_not_called()
        heartbeat.assert_called_once()
        extender.return_value.start.assert_not_called()
        extender.return_value.stop.assert_called_once()
    assert len(polls) == 1
