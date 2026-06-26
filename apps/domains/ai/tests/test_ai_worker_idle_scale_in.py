from __future__ import annotations

from unittest.mock import patch

from django.test import SimpleTestCase

from academy.framework.workers import ai_sqs_worker


class _Queue:
    def __init__(self, counts: dict[str, int]) -> None:
        self.counts = counts

    def get_counts(self, tier: str = "basic") -> dict[str, int]:
        return self.counts


class _SequenceQueue:
    def __init__(self, counts_sequence: list[dict[str, int]]) -> None:
        self.counts_sequence = counts_sequence
        self.calls = 0

    def get_counts(self, tier: str = "basic") -> dict[str, int]:
        idx = min(self.calls, len(self.counts_sequence) - 1)
        self.calls += 1
        return self.counts_sequence[idx]


class _TierQueue:
    def __init__(self, counts_by_tier: dict[str, dict[str, int]]) -> None:
        self.counts_by_tier = counts_by_tier

    def get_counts(self, tier: str = "basic") -> dict[str, int]:
        return self.counts_by_tier[tier]


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

        with (
            patch.object(ai_sqs_worker, "IDLE_SCALE_IN_COUNT_TIERS", ("basic",)),
            patch.object(ai_sqs_worker, "IDLE_SCALE_IN_CONFIRM_SECONDS", 0),
            patch.object(ai_sqs_worker, "_active_running_ai_jobs_exist", return_value=False),
            patch.object(ai_sqs_worker, "scale_down_ai_worker_asg_to_zero_if_idle") as scale_down,
        ):
            self.assertFalse(ai_sqs_worker._try_idle_scale_in(queue, "basic"))

        scale_down.assert_not_called()

    def test_idle_scale_in_calls_asg_adapter_when_queue_is_empty(self):
        counts = {"visible": 0, "not_visible": 0, "delayed": 0}
        queue = _Queue(counts)

        with (
            patch.object(ai_sqs_worker, "IDLE_SCALE_IN_COUNT_TIERS", ("basic",)),
            patch.object(ai_sqs_worker, "IDLE_SCALE_IN_CONFIRM_SECONDS", 0),
            patch.object(ai_sqs_worker, "_active_running_ai_jobs_exist", return_value=False),
            patch.object(
                ai_sqs_worker,
                "scale_down_ai_worker_asg_to_zero_if_idle",
                return_value=True,
            ) as scale_down,
        ):
            self.assertTrue(ai_sqs_worker._try_idle_scale_in(queue, "basic"))

        scale_down.assert_called_once_with(counts)

    def test_idle_scale_in_rechecks_before_asg_scale_down(self):
        queue = _SequenceQueue(
            [
                {"visible": 0, "not_visible": 0, "delayed": 0},
                {"visible": 0, "not_visible": 1, "delayed": 0},
            ]
        )

        with (
            patch.object(ai_sqs_worker, "IDLE_SCALE_IN_COUNT_TIERS", ("basic",)),
            patch.object(ai_sqs_worker, "IDLE_SCALE_IN_CONFIRM_SECONDS", 0),
            patch.object(ai_sqs_worker, "_active_running_ai_jobs_exist", return_value=False),
            patch.object(ai_sqs_worker, "scale_down_ai_worker_asg_to_zero_if_idle") as scale_down,
        ):
            self.assertFalse(ai_sqs_worker._try_idle_scale_in(queue, "basic"))

        scale_down.assert_not_called()

    def test_idle_scale_in_checks_all_ai_queue_tiers(self):
        queue = _TierQueue(
            {
                "basic": {"visible": 0, "not_visible": 0, "delayed": 0},
                "lite": {"visible": 0, "not_visible": 1, "delayed": 0},
                "premium": {"visible": 0, "not_visible": 0, "delayed": 0},
            }
        )

        with (
            patch.object(ai_sqs_worker, "IDLE_SCALE_IN_COUNT_TIERS", ("basic", "lite", "premium")),
            patch.object(ai_sqs_worker, "IDLE_SCALE_IN_CONFIRM_SECONDS", 0),
            patch.object(ai_sqs_worker, "_active_running_ai_jobs_exist", return_value=False),
            patch.object(ai_sqs_worker, "scale_down_ai_worker_asg_to_zero_if_idle") as scale_down,
        ):
            self.assertFalse(ai_sqs_worker._try_idle_scale_in(queue, "basic"))

        scale_down.assert_not_called()

    def test_idle_scale_in_skips_when_running_ai_job_has_active_lease(self):
        queue = _Queue({"visible": 0, "not_visible": 0, "delayed": 0})

        with (
            patch.object(ai_sqs_worker, "IDLE_SCALE_IN_COUNT_TIERS", ("basic",)),
            patch.object(ai_sqs_worker, "IDLE_SCALE_IN_CONFIRM_SECONDS", 0),
            patch.object(ai_sqs_worker, "_active_running_ai_jobs_exist", return_value=True),
            patch.object(ai_sqs_worker, "scale_down_ai_worker_asg_to_zero_if_idle") as scale_down,
        ):
            self.assertFalse(ai_sqs_worker._try_idle_scale_in(queue, "basic"))

        scale_down.assert_not_called()
