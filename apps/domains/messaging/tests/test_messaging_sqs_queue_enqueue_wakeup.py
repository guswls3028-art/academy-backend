from __future__ import annotations

from unittest.mock import patch

from django.test import SimpleTestCase

from apps.domains.messaging.sqs_queue import MessagingSQSQueue


class _QueueClient:
    def __init__(self, *, send_result: bool = True) -> None:
        self.send_result = send_result

    def send_message(self, *, queue_name: str, message: dict) -> bool:
        self.queue_name = queue_name
        self.message = message
        return self.send_result


def _enqueue(queue: MessagingSQSQueue) -> bool:
    return queue.enqueue(
        tenant_id=1,
        to="01031217466",
        text="계정 복구 안내",
        message_mode="alimtalk",
        template_id="account_recovery",
        event_type="account_recovery",
        target_type="account",
        target_id="student:2222",
        target_name="복구학생",
        source_tenant_id=1,
    )


class MessagingSQSQueueEnqueueWakeupTests(SimpleTestCase):
    def test_enqueue_wakes_messaging_worker_capacity_after_successful_send(self):
        queue_client = _QueueClient(send_result=True)

        with (
            patch("apps.domains.messaging.sqs_queue.get_queue_client", return_value=queue_client),
            patch(
                "academy.adapters.compute.ec2_control.ensure_messaging_worker_asg_min_capacity",
                return_value=True,
            ) as ensure_capacity,
        ):
            self.assertTrue(_enqueue(MessagingSQSQueue()))

        ensure_capacity.assert_called_once_with(min_capacity=1)

    def test_enqueue_does_not_wake_workers_when_send_fails(self):
        queue_client = _QueueClient(send_result=False)

        with (
            patch("apps.domains.messaging.sqs_queue.get_queue_client", return_value=queue_client),
            patch(
                "academy.adapters.compute.ec2_control.ensure_messaging_worker_asg_min_capacity",
            ) as ensure_capacity,
        ):
            self.assertFalse(_enqueue(MessagingSQSQueue()))

        ensure_capacity.assert_not_called()

    def test_wake_can_be_disabled_for_non_production_queue_paths(self):
        queue_client = _QueueClient(send_result=True)

        with (
            patch("apps.domains.messaging.sqs_queue.get_queue_client", return_value=queue_client),
            patch(
                "academy.adapters.compute.ec2_control.ensure_messaging_worker_asg_min_capacity",
            ) as ensure_capacity,
        ):
            self.assertTrue(_enqueue(MessagingSQSQueue(wake_messaging_workers=False)))

        ensure_capacity.assert_not_called()
