from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from academy.adapters.compute.ec2_control import (
    scale_down_ai_worker_asg_to_baseline_if_idle,
)


class AIWorkerCapacityControlTests(SimpleTestCase):
    def test_idle_worker_scales_burst_capacity_to_warm_baseline(self):
        autoscaling = Mock()
        autoscaling.describe_auto_scaling_groups.return_value = {
            "AutoScalingGroups": [
                {"MinSize": 1, "DesiredCapacity": 3, "MaxSize": 5}
            ]
        }

        with patch(
            "academy.adapters.compute.ec2_control._aws_client",
            return_value=autoscaling,
        ):
            assert scale_down_ai_worker_asg_to_baseline_if_idle(
                {"visible": 0, "not_visible": 0, "delayed": 0}
            )

        autoscaling.set_desired_capacity.assert_called_once_with(
            AutoScalingGroupName="academy-v1-ai-worker-asg",
            DesiredCapacity=1,
            HonorCooldown=False,
        )

    def test_idle_worker_does_not_scale_below_configured_minimum(self):
        autoscaling = Mock()
        autoscaling.describe_auto_scaling_groups.return_value = {
            "AutoScalingGroups": [
                {"MinSize": 2, "DesiredCapacity": 2, "MaxSize": 5}
            ]
        }

        with patch(
            "academy.adapters.compute.ec2_control._aws_client",
            return_value=autoscaling,
        ):
            assert scale_down_ai_worker_asg_to_baseline_if_idle(
                {"visible": 0, "not_visible": 0, "delayed": 0}
            )

        autoscaling.set_desired_capacity.assert_not_called()

    def test_busy_queue_never_requests_scale_in(self):
        autoscaling = Mock()

        with patch(
            "academy.adapters.compute.ec2_control._aws_client",
            return_value=autoscaling,
        ):
            assert not scale_down_ai_worker_asg_to_baseline_if_idle(
                {"visible": 1, "not_visible": 0, "delayed": 0}
            )

        autoscaling.describe_auto_scaling_groups.assert_not_called()
