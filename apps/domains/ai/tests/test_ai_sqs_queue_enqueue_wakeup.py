from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.support.ai.services.sqs_queue import AISQSQueue


@dataclass
class _Job:
    job_id: str = "job-1"
    job_type: str = "matchup_analysis"
    status: str = "PENDING"
    tier: str = "basic"
    payload: dict = field(default_factory=dict)
    tenant_id: str = "1"
    source_domain: str = "matchup"
    source_id: str = "1"


class _QueueClient:
    def __init__(self, *, send_result: bool = True) -> None:
        self.send_result = send_result

    def send_message(self, *, queue_name: str, message: dict) -> bool:
        self.queue_name = queue_name
        self.message = message
        return self.send_result


class AISQSQueueEnqueueWakeupTests(SimpleTestCase):
    def test_enqueue_wakes_ai_worker_capacity_after_successful_send(self):
        queue_client = _QueueClient(send_result=True)

        with (
            patch("apps.support.ai.services.sqs_queue.get_queue_client", return_value=queue_client),
            patch(
                "academy.adapters.compute.ec2_control.ensure_ai_worker_asg_min_capacity",
                return_value=True,
            ) as ensure_capacity,
        ):
            self.assertTrue(AISQSQueue().enqueue(_Job()))

        ensure_capacity.assert_called_once_with(min_capacity=3)

    def test_enqueue_does_not_wake_workers_when_send_fails(self):
        queue_client = _QueueClient(send_result=False)

        with (
            patch("apps.support.ai.services.sqs_queue.get_queue_client", return_value=queue_client),
            patch(
                "academy.adapters.compute.ec2_control.ensure_ai_worker_asg_min_capacity",
            ) as ensure_capacity,
        ):
            self.assertFalse(AISQSQueue().enqueue(_Job()))

        ensure_capacity.assert_not_called()

    def test_tools_queue_override_does_not_wake_ai_workers(self):
        queue_client = _QueueClient(send_result=True)

        with (
            patch("apps.support.ai.services.sqs_queue.get_queue_client", return_value=queue_client),
            patch(
                "academy.adapters.compute.ec2_control.ensure_ai_worker_asg_min_capacity",
            ) as ensure_capacity,
        ):
            queue = AISQSQueue(queue_name_override="academy-v1-tools-queue", wake_ai_workers=False)
            self.assertTrue(queue.enqueue(_Job(job_type="ppt_generate")))

        ensure_capacity.assert_not_called()
