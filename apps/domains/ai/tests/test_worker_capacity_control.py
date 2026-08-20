from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from academy.adapters.compute.ec2_control import (
    _capacity_ensure_cache,
    ensure_tools_worker_asg_min_capacity,
)
from libs.queue.client import SQSQueueClient


class WorkerCapacityControlTests(SimpleTestCase):
    def setUp(self):
        _capacity_ensure_cache.clear()

    def tearDown(self):
        _capacity_ensure_cache.clear()

    @patch("academy.adapters.compute.ec2_control.boto3.client")
    def test_tools_wakeup_uses_bounded_aws_timeouts(self, client_factory):
        autoscaling = MagicMock()
        autoscaling.describe_auto_scaling_groups.return_value = {
            "AutoScalingGroups": [
                {
                    "DesiredCapacity": 0,
                    "MaxSize": 1,
                }
            ]
        }
        client_factory.return_value = autoscaling

        self.assertTrue(ensure_tools_worker_asg_min_capacity())

        _, kwargs = client_factory.call_args
        config = kwargs["config"]
        self.assertEqual(config.connect_timeout, 2)
        self.assertEqual(config.read_timeout, 3)
        self.assertEqual(config.retries["total_max_attempts"], 1)
        autoscaling.set_desired_capacity.assert_called_once()

    @patch("academy.adapters.compute.ec2_control._aws_client")
    def test_burst_capacity_checks_share_one_asg_read(self, client_factory):
        autoscaling = MagicMock()
        autoscaling.describe_auto_scaling_groups.return_value = {
            "AutoScalingGroups": [
                {
                    "DesiredCapacity": 1,
                    "MaxSize": 2,
                }
            ]
        }
        client_factory.return_value = autoscaling

        self.assertTrue(ensure_tools_worker_asg_min_capacity())
        self.assertTrue(ensure_tools_worker_asg_min_capacity())
        self.assertTrue(ensure_tools_worker_asg_min_capacity())

        autoscaling.describe_auto_scaling_groups.assert_called_once_with(
            AutoScalingGroupNames=["academy-v1-tools-worker-asg"]
        )
        autoscaling.set_desired_capacity.assert_not_called()

    @patch("boto3.client")
    @patch("libs.queue.client.os.getenv", side_effect=lambda _name, default=None: default)
    def test_api_enqueue_sqs_client_uses_bounded_timeouts(
        self,
        _getenv,
        client_factory,
    ):
        SQSQueueClient(request_timeout_seconds=3)

        _, kwargs = client_factory.call_args
        config = kwargs["config"]
        self.assertEqual(config.connect_timeout, 2)
        self.assertEqual(config.read_timeout, 3)
        self.assertEqual(config.retries["total_max_attempts"], 1)
