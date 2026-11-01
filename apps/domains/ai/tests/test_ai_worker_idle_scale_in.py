from __future__ import annotations

from datetime import timedelta
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from academy.framework.workers import ai_sqs_worker
from apps.domains.ai.models import AIJobModel
from libs.queue import QueueUnavailableError


class _Queue:
    def __init__(self, counts: dict[str, int]) -> None:
        self.counts = counts

    def get_counts(self, tier: str = "basic") -> dict[str, int]:
        return self.counts


class _SequenceQueue:
    def __init__(self, counts_sequence: list[dict[str, int] | Exception]) -> None:
        self.counts_sequence = counts_sequence
        self.calls = 0

    def get_counts(self, tier: str = "basic") -> dict[str, int]:
        idx = min(self.calls, len(self.counts_sequence) - 1)
        self.calls += 1
        result = self.counts_sequence[idx]
        if isinstance(result, Exception):
            raise result
        return result


class _TierQueue:
    def __init__(self, counts_by_tier: dict[str, dict[str, int]]) -> None:
        self.counts_by_tier = counts_by_tier

    def get_counts(self, tier: str = "basic") -> dict[str, int]:
        return self.counts_by_tier[tier]


class AIWorkerIdleScaleInTests(SimpleTestCase):
    def test_idle_baseline_keeps_polling_but_burst_scale_in_exits(self):
        for desired, expected_polls in ((1, 2), (3, 1)):
            with self.subTest(desired=desired):
                autoscaling = Mock()
                autoscaling.describe_auto_scaling_groups.return_value = {
                    "AutoScalingGroups": [{"MinSize": 1, "DesiredCapacity": desired, "MaxSize": 5}]
                }
                queue = _Queue({"visible": 0, "not_visible": 0, "delayed": 0})
                polls = 0

                def poll(*args, **kwargs):
                    nonlocal polls
                    polls += 1
                    if polls == 2:
                        ai_sqs_worker._shutdown = True
                    return None, "basic"

                with (
                    patch.object(ai_sqs_worker, "_shutdown", False),
                    patch.object(ai_sqs_worker, "IDLE_SCALE_IN_ENABLED", True),
                    patch.object(ai_sqs_worker, "IDLE_EMPTY_POLLS_BEFORE_SCALE_IN", 1),
                    patch.object(ai_sqs_worker, "IDLE_SCALE_IN_COUNT_TIERS", ("basic",)),
                    patch.object(ai_sqs_worker, "IDLE_SCALE_IN_CONFIRM_SECONDS", 0),
                    patch.object(ai_sqs_worker, "_active_running_ai_jobs_exist", return_value=False),
                    patch("academy.adapters.compute.ec2_control._aws_client", return_value=autoscaling),
                    patch.object(ai_sqs_worker, "_weighted_poll", side_effect=poll),
                    patch.object(ai_sqs_worker, "prepare_ai_job") as claim,
                    patch.object(ai_sqs_worker, "SQSVisibilityExtender") as extender,
                    patch.object(ai_sqs_worker, "close_old_connections"),
                    patch.object(ai_sqs_worker, "_release_db_connections"),
                    patch.object(ai_sqs_worker.signal, "signal"),
                    patch("apps.shared.utils.heartbeat.beat") as heartbeat,
                ):
                    self.assertEqual(ai_sqs_worker.run_ai_sqs_worker(queue=queue), 0)

                self.assertEqual(polls, expected_polls)
                self.assertEqual(heartbeat.call_count, expected_polls)
                autoscaling.describe_auto_scaling_groups.assert_called_once()
                claim.assert_not_called()
                extender.return_value.stop.assert_called_once()
                if desired == 1:
                    autoscaling.set_desired_capacity.assert_not_called()
                else:
                    autoscaling.set_desired_capacity.assert_called_once_with(
                        AutoScalingGroupName="academy-v1-ai-worker-asg",
                        DesiredCapacity=1,
                        HonorCooldown=False,
                    )

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
            patch.object(ai_sqs_worker, "scale_down_ai_worker_asg_to_baseline_if_idle") as scale_down,
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
                "scale_down_ai_worker_asg_to_baseline_if_idle",
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
            patch.object(ai_sqs_worker, "scale_down_ai_worker_asg_to_baseline_if_idle") as scale_down,
        ):
            self.assertFalse(ai_sqs_worker._try_idle_scale_in(queue, "basic"))

        scale_down.assert_not_called()

    def test_idle_scale_in_skips_when_initial_queue_health_is_unavailable(self):
        queue = _SequenceQueue([QueueUnavailableError("queue unavailable")])

        with (
            patch.object(ai_sqs_worker, "IDLE_SCALE_IN_COUNT_TIERS", ("basic",)),
            patch.object(ai_sqs_worker, "scale_down_ai_worker_asg_to_baseline_if_idle") as scale_down,
        ):
            self.assertFalse(ai_sqs_worker._try_idle_scale_in(queue, "basic"))

        scale_down.assert_not_called()

    def test_idle_scale_in_skips_when_confirm_queue_health_is_unavailable(self):
        queue = _SequenceQueue(
            [
                {"visible": 0, "not_visible": 0, "delayed": 0},
                QueueUnavailableError("confirm queue unavailable"),
            ]
        )

        with (
            patch.object(ai_sqs_worker, "IDLE_SCALE_IN_COUNT_TIERS", ("basic",)),
            patch.object(ai_sqs_worker, "IDLE_SCALE_IN_CONFIRM_SECONDS", 0),
            patch.object(ai_sqs_worker, "_active_running_ai_jobs_exist", return_value=False),
            patch.object(ai_sqs_worker, "scale_down_ai_worker_asg_to_baseline_if_idle") as scale_down,
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
            patch.object(ai_sqs_worker, "scale_down_ai_worker_asg_to_baseline_if_idle") as scale_down,
        ):
            self.assertFalse(ai_sqs_worker._try_idle_scale_in(queue, "basic"))

        scale_down.assert_not_called()

    def test_idle_scale_in_skips_when_running_ai_job_has_active_lease(self):
        queue = _Queue({"visible": 0, "not_visible": 0, "delayed": 0})

        with (
            patch.object(ai_sqs_worker, "IDLE_SCALE_IN_COUNT_TIERS", ("basic",)),
            patch.object(ai_sqs_worker, "IDLE_SCALE_IN_CONFIRM_SECONDS", 0),
            patch.object(ai_sqs_worker, "_active_running_ai_jobs_exist", return_value=True),
            patch.object(ai_sqs_worker, "scale_down_ai_worker_asg_to_baseline_if_idle") as scale_down,
        ):
            self.assertFalse(ai_sqs_worker._try_idle_scale_in(queue, "basic"))

        scale_down.assert_not_called()


class AIWorkerActiveJobQueryTests(TestCase):
    def test_active_job_check_treats_recent_pending_job_as_work(self):
        AIJobModel.objects.create(
            job_id="pending-matchup",
            job_type="matchup_analysis",
            status="PENDING",
            tier="basic",
            tenant_id="1",
            source_domain="matchup",
            source_id="1",
        )

        with patch.object(ai_sqs_worker, "IDLE_SCALE_IN_PENDING_JOB_WINDOW_SECONDS", 21600):
            self.assertTrue(ai_sqs_worker._active_running_ai_jobs_exist())

    def test_active_job_check_ignores_stale_pending_job(self):
        job = AIJobModel.objects.create(
            job_id="stale-pending-matchup",
            job_type="matchup_analysis",
            status="PENDING",
            tier="basic",
            tenant_id="1",
            source_domain="matchup",
            source_id="1",
        )
        AIJobModel.objects.filter(pk=job.pk).update(
            updated_at=timezone.now() - timedelta(hours=7)
        )

        with patch.object(ai_sqs_worker, "IDLE_SCALE_IN_PENDING_JOB_WINDOW_SECONDS", 21600):
            self.assertFalse(ai_sqs_worker._active_running_ai_jobs_exist())
