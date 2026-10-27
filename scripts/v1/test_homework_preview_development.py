"""Isolation and failure boundaries for the dedicated preview runtime."""
import importlib.util
from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
spec = importlib.util.spec_from_file_location("homework_preview_development", Path(__file__).with_name("homework_preview_development.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class HomeworkRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.config = {"global": {"region": "ap-northeast-2", "accountId": "809466760795"},
            "apiDevelopment": {"workersEnvParameter": "/academy/workers/development/env", "accessMode": "ssm-only", "roleName": "academy-api-development-role"},
            "homeworkPreviewDevelopment": {"queueName": "academy-development-homework-video-queue", "jobDefinitionPrefix": "academy-development-homework-video-",
                "computeEnvironmentName": "academy-development-homework-video-ce", "jobRoleName": "academy-development-homework-video-job-role",
                "executionRoleName": "academy-development-homework-video-execution-role", "apiPolicyName": module.POLICY_NAME,
                "logGroupName": "/aws/batch/academy-homework-development", "maxVcpus": "4"}}
        self.image = "809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker@sha256:" + "a" * 64
        self.release = "sha-" + "b" * 40 + "-run-123-1"

    def runtime(self, apply=False):
        return module.Runtime(self.config, self.image, self.release, 17, apply)

    def test_pin_changes_for_either_release_or_parameter_version(self):
        first = module.definition_name(self.release, 17)
        self.assertNotEqual(first, module.definition_name(self.release, 18))
        self.assertNotEqual(first, module.definition_name(self.release.replace("123", "124"), 17))
        self.assertRegex(first, r"^academy-development-homework-video-[a-f0-9]{12}-v17$")

    def test_mutation_requires_apply_and_fresh_shared_lock(self):
        with patch.object(module.subprocess, "run") as cli:
            with self.assertRaisesRegex(RuntimeError, "apply_with_shared_lock"):
                self.runtime().aws("iam", "put-role-policy", {}, mutate=True)
            with patch.dict("os.environ", {"ACADEMY_DEPLOY_LOCK_OWNER": "owned"}), patch.object(module, "assert_owned", side_effect=RuntimeError("foreign lock")):
                with self.assertRaisesRegex(RuntimeError, "foreign lock"):
                    self.runtime(True).aws("iam", "put-role-policy", {}, mutate=True)
            cli.assert_not_called()

    def test_no_production_queue_role_or_mutable_image_is_accepted(self):
        for key in ("queueName", "jobRoleName", "computeEnvironmentName"):
            original = self.config["homeworkPreviewDevelopment"][key]
            self.config["homeworkPreviewDevelopment"][key] = "academy-production"
            with self.assertRaises(ValueError): self.runtime()
            self.config["homeworkPreviewDevelopment"][key] = original
        self.image = self.image.split("@")[0] + ":latest"
        with self.assertRaisesRegex(ValueError, "immutable"): self.runtime()

    def test_secret_cli_diagnostics_are_never_relayed(self):
        from types import SimpleNamespace
        with patch.object(module.subprocess, "run", return_value=SimpleNamespace(returncode=1, stderr="secret-url-password An error occurred (AccessDenied) details", stdout="secret")):
            with self.assertRaises(RuntimeError) as error:
                self.runtime().aws("ssm", "send-command")
        self.assertEqual(str(error.exception), "ssm:send-command:AccessDenied")

    def test_cleanup_is_limited_to_two_objects_of_exact_batch_job(self):
        command = module.Runtime.cleanup_command("12345678-1234-1234-1234-123456789abc")
        self.assertIn("qa-homework-preview/12345678-1234-1234-1234-123456789abc/", command)
        self.assertIn("('source.mp4', 'preview.mp4')", command)
        self.assertNotIn("delete_objects", command)
        with self.assertRaises(ValueError): module.Runtime.cleanup_command("../../production")

    def test_submit_waits_only_for_explicit_iam_denial_then_creates_one_job(self):
        from botocore.exceptions import ClientError
        payload = {"jobName": "qa-homework-preview-owned", "jobQueue": "owned-queue", "jobDefinition": "owned-definition:1"}
        denial = ClientError({"Error": {"Code": "AccessDeniedException"}}, "SubmitJob")
        output = io.StringIO()
        with patch("boto3.client") as factory, patch("time.monotonic", side_effect=[0, 1, 6]), patch("time.sleep") as sleep:
            factory.return_value.submit_job.side_effect = [denial, denial, {"jobId": "owned-job"}]
            with redirect_stdout(output):
                exec(module.canary_submission_script("ap-northeast-2", payload), {})
            self.assertEqual(factory.return_value.submit_job.call_count, 3)
            self.assertTrue(all(call.kwargs == payload for call in factory.return_value.submit_job.call_args_list))
            self.assertEqual(sleep.call_count, 2)
            self.assertEqual(factory.call_args.kwargs["config"].retries, {"total_max_attempts": 1})
        self.assertEqual(json.loads(output.getvalue()), {"job_id": "owned-job"})

    def test_permanent_denial_exhausts_deadline_without_reporting_success(self):
        from botocore.exceptions import ClientError
        denial = ClientError({"Error": {"Code": "AccessDeniedException"}}, "SubmitJob")
        output = io.StringIO()
        with patch("boto3.client") as factory, patch("time.monotonic", side_effect=[0, 1, 45]), patch("time.sleep") as sleep:
            factory.return_value.submit_job.side_effect = denial
            with redirect_stdout(output), self.assertRaises(ClientError):
                exec(module.canary_submission_script("ap-northeast-2", {}), {})
            self.assertEqual(factory.return_value.submit_job.call_count, 2)
            sleep.assert_called_once_with(5)
        self.assertEqual(output.getvalue(), "")

    def test_uncertain_submit_response_and_other_failures_are_never_retried(self):
        from botocore.exceptions import ClientError, ReadTimeoutError
        failures = [ReadTimeoutError(endpoint_url="https://batch.example"),
            ClientError({"Error": {"Code": "ServerException"}}, "SubmitJob"),
            ClientError({"Error": {"Code": "ClientException"}}, "SubmitJob")]
        for failure in failures:
            with self.subTest(error=type(failure).__name__), patch("boto3.client") as factory, patch("time.sleep") as sleep:
                factory.return_value.submit_job.side_effect = failure
                with self.assertRaises(type(failure)):
                    exec(module.canary_submission_script("ap-northeast-2", {}), {})
                factory.return_value.submit_job.assert_called_once()
                sleep.assert_not_called()

    def test_isolation_grant_and_oidc_policy_stay_bounded(self):
        grant = json.loads((ROOT / "scripts/v1/templates/iam/policy_homework_preview_development_submit.json").read_text())
        self.assertEqual(len(grant["Statement"]), 1)
        self.assertEqual(grant["Statement"][0]["Action"], "batch:SubmitJob")
        self.assertTrue(all("academy-development-homework-video-" in r for r in grant["Statement"][0]["Resource"]))
        policy = json.loads((ROOT / "infra/worker_asg/iam_policy_gha_development_deploy.json").read_text())
        self.assertLess(len(json.dumps(policy, separators=(",", ":"))), 6144)
        owned = [s for s in policy["Statement"] if s["Sid"].startswith("Homework")]
        self.assertFalse(any("batch:SubmitJob" in s["Action"] for s in owned))
        self.assertFalse(any("iam:AttachRolePolicy" in s["Action"] for s in owned))

    def test_aws_empty_log_secrets_readback_is_equivalent_without_relaxing_identity(self):
        expected = {"image": self.image, "jobRoleArn": "owned-job-role", "executionRoleArn": "owned-execution-role",
            "environment": [{"name": "BATCH_SSM_ENV", "value": "/academy/workers/development/env:17"}],
            "resourceRequirements": [{"type": "VCPU", "value": "2"}, {"type": "MEMORY", "value": "4096"}],
            "networkConfiguration": {"assignPublicIp": "ENABLED"},
            "logConfiguration": {"logDriver": "awslogs", "options": {"awslogs-group": "/aws/batch/academy-homework-development"}}}
        # Observed AWS DescribeJobDefinitions response defaults, not caller input.
        actual = deepcopy(expected)
        actual.update(volumes=[], mountPoints=[], ulimits=[], secrets=[])
        actual["logConfiguration"]["secretOptions"] = []
        self.assertTrue(module.container_properties_match(actual, expected))
        self.assertIn("secretOptions", actual["logConfiguration"])
        for field, value in (("image", self.image + ":changed"), ("jobRoleArn", "foreign-role"),
                ("executionRoleArn", "foreign-role"), ("environment", []), ("resourceRequirements", []),
                ("networkConfiguration", {"assignPublicIp": "DISABLED"}),
                ("volumes", [{"name": "foreign-volume"}]), ("secrets", [{"name": "foreign-secret"}]),
                ("mountPoints", [{"containerPath": "/etc"}]), ("ulimits", [{"name": "core", "hardLimit": -1}])):
            with self.subTest(field=field):
                changed = deepcopy(actual)
                changed[field] = value
                self.assertFalse(module.container_properties_match(changed, expected))
        for changed_log in ({**actual["logConfiguration"], "secretOptions": [{"name": "token", "valueFrom": "foreign-secret"}]},
                            {**actual["logConfiguration"], "options": {"awslogs-group": "foreign-logs"}}):
            self.assertFalse(module.container_properties_match({**actual, "logConfiguration": changed_log}, expected))


if __name__ == "__main__": unittest.main()
