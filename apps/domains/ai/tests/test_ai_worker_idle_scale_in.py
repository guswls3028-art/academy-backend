from __future__ import annotations

from unittest.mock import patch

from django.test import SimpleTestCase

from academy.framework.workers import ai_sqs_worker


class _Queue:
    def __init__(self, counts: dict[str, int]) -> None:
        self.counts = counts

    def get_counts(self, tier: str = "basic") -> dict[str, int]:
        return self.counts


class AIWorkerIdleScaleInTests(SimpleTestCase):
    def test_queue_counts_are_idle_only_when_all_depths_zero(self):
        self.assertTrue(
            ai_sqs_worker._queue_counts_are_idle(
                {"visible": 0, "not_visible": 0, "delayed": 0}
            )
        )
        self.assertFalse(
            ai_sqs_worker._queue_counts_are_idle(
                {"visible": 0, "not_visible": 1, "delayed": 0}
            )
        )

    def test_idle_scale_in_skips_when_inflight_messages_exist(self):
        queue = _Queue({"visible": 0, "not_visible": 1, "delayed": 0})

        with patch.object(
            ai_sqs_worker, "scale_down_ai_worker_asg_to_zero_if_idle"
        ) as scale_down:
            self.assertFalse(ai_sqs_worker._try_idle_scale_in(queue, "basic"))

        scale_down.assert_not_called()

    def test_idle_scale_in_calls_asg_adapter_when_queue_is_empty(self):
        counts = {"visible": 0, "not_visible": 0, "delayed": 0}
        queue = _Queue(counts)

        with patch.object(
            ai_sqs_worker,
            "scale_down_ai_worker_asg_to_zero_if_idle",
            return_value=True,
        ) as scale_down:
            self.assertTrue(ai_sqs_worker._try_idle_scale_in(queue, "basic"))

        scale_down.assert_called_once_with(counts)
