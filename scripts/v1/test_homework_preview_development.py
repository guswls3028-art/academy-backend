"""Isolation and failure boundaries for the dedicated preview runtime."""
import importlib.util
from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
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
            "apiDevelopment": {"workersEnvParameter": "/academy/workers/development/env", "accessMode": "ssm-only", "roleName": "academy-api-development-role",
                "instanceName": "academy-v1-api-development", "managedByTag": "academy-api-development"},
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

    def test_canary_completes_with_submit_only_api_permission_and_exact_cleanup(self):
        from botocore.exceptions import ClientError
        runtime = self.runtime()
        resources = {"queue": "owned-queue", "definition": "owned-definition:1"}
        job_id = "12345678-1234-1234-1234-123456789abc"
        cleanup = {"remaining_objects": 0, "tenant_rows_created": 0, "user_rows_created": 0}

        def submit(**payload):
            # Reproduce Batch's extra authorization check on tagged submission.
            if "tags" in payload:
                raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "batch:TagResource denied"}}, "SubmitJob")
            self.assertEqual(payload["jobQueue"], resources["queue"])
            self.assertEqual(payload["jobDefinition"], resources["definition"])
            self.assertTrue(payload["jobName"].startswith("qa-homework-preview-"))
            return {"jobId": job_id}

        def remote(command):
            if command == runtime.cleanup_command(job_id):
                return cleanup
            source = command.split("<<'HOMEWORK_CANARY'\n", 1)[1].rsplit("\nHOMEWORK_CANARY", 1)[0]
            output = io.StringIO()
            with patch("boto3.client") as factory, patch("time.monotonic", side_effect=[0, 45]), redirect_stdout(output):
                factory.return_value.submit_job.side_effect = submit
                exec(source, {})
                factory.return_value.submit_job.assert_called_once()
            return json.loads(output.getvalue())

        with patch.object(runtime, "aws", side_effect=[
            {"Reservations": [{"Instances": [{"InstanceId": "owned-instance"}]}]},
            {"jobs": [{"status": "SUCCEEDED"}]},
        ]), patch.object(runtime, "remote_json", side_effect=remote) as remote_call, patch.object(runtime, "verify_job", return_value={"status": "SUCCEEDED"}) as verify:
            self.assertEqual(runtime.canary(resources, "academy"), {"status": "SUCCEEDED"})
            verify.assert_called_once_with(resources, job_id)
            self.assertEqual(remote_call.call_args_list[-1].args, (runtime.cleanup_command(job_id),))
        self.assertEqual(runtime.evidence, {"job_id": job_id, "cleanup": cleanup})

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


class HomeworkCanaryBoundaryTests(unittest.TestCase):
    def setUp(self):
        from apps.worker.video_worker import homework_preview_canary
        from unittest.mock import MagicMock
        self.canary = homework_preview_canary
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.fetchone.return_value = (
            "academy_api_development", "academy_api_development_app", False,
        )

    def test_catalog_denial_does_not_depend_on_connection_error_sqlstate(self):
        self.canary.verify_database_boundary(self.connection, "postgres")
        query, parameters = self.cursor.execute.call_args.args
        self.assertIn("has_database_privilege(current_user, %s, 'CONNECT')", query)
        self.assertEqual(parameters, ["postgres"])
        self.connection.Database.connect.assert_not_called()

    def test_wrong_identity_or_unknown_connect_privilege_blocks_canary(self):
        for row in (("postgres", "academy_api_development_app", False),
                    ("academy_api_development", "production_role", False),
                    ("academy_api_development", "academy_api_development_app", True),
                    ("academy_api_development", "academy_api_development_app", None),
                    ("academy_api_development", "academy_api_development_app", 0)):
            with self.subTest(row=row), self.assertRaises(RuntimeError):
                self.cursor.fetchone.return_value = row
                self.canary.verify_database_boundary(self.connection, "postgres")

    def test_catalog_read_failure_is_never_treated_as_denial(self):
        self.cursor.execute.side_effect = RuntimeError("transport failure")
        with self.assertRaisesRegex(RuntimeError, "transport failure"):
            self.canary.verify_database_boundary(self.connection, "postgres")

    def test_failure_log_only_contains_fixed_guard_codes(self):
        from botocore.exceptions import ClientError
        expected = self.canary.failure_record(RuntimeError("production_database_denial_unproven"))
        self.assertEqual(expected["failure_code"], "production_database_denial_unproven")
        for error in (RuntimeError("secret-connection-url"),
                      ValueError("production_database_denial_unproven"),
                      ClientError({"Error": {"Code": "Denied", "Message": "secret-connection-url"}}, "Read")):
            with self.subTest(error_type=type(error).__name__):
                record = self.canary.failure_record(error)
                self.assertEqual(record["failure_code"], "unexpected_error")
                self.assertNotIn("secret-connection-url", json.dumps(record))

    def test_storage_denial_matches_development_gate_and_rejects_unknown_errors(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from botocore.exceptions import ClientError
        settings = SimpleNamespace(R2_AI_BUCKET="academy-development-artifacts",
                                   VIDEO_BATCH_JOB_QUEUE="", VIDEO_BATCH_JOB_DEFINITION="")
        storage, ssm = MagicMock(), MagicMock()
        ssm.get_parameter.side_effect = ClientError({"Error": {"Code": "AccessDenied"}}, "GetParameter")
        storage.put_object.side_effect = RuntimeError("reached_synthetic_write")
        storage.list_objects_v2.return_value = {"KeyCount": 0}
        environment = {"AWS_BATCH_JOB_ID": "12345678-1234-1234-1234-123456789abc",
                       "ACADEMY_RUNTIME_ENV": "development", "ACADEMY_DEVELOPMENT_RELEASE_ID": "owned-release",
                       "AWS_DEFAULT_REGION": "ap-northeast-2"}
        with patch.dict("os.environ", environment), patch("django.db.connections", {"default": self.connection}), \
                patch("django.conf.settings", settings), \
                patch("boto3.client", return_value=ssm), \
                patch.dict(sys.modules, {"apps.infrastructure.storage.r2": SimpleNamespace(_get_s3_client=lambda **_: storage)}):
            for code in ("403", "404", "AccessDenied", "NoSuchBucket", "401", "RequestTimeout", "InternalError"):
                with self.subTest(code=code):
                    storage.reset_mock()
                    storage.head_bucket.side_effect = ClientError({"Error": {"Code": code}}, "HeadBucket")
                    recognized = code in {"403", "404", "AccessDenied", "NoSuchBucket"}
                    expected = "reached_synthetic_write" if recognized else "production_bucket_denial_unproven"
                    with self.assertRaisesRegex(RuntimeError, expected):
                        self.canary.run(release_id="owned-release", production_database="postgres")
                    self.assertEqual(storage.put_object.called, recognized)
            storage.reset_mock()
            storage.head_bucket.side_effect = None
            with self.assertRaisesRegex(RuntimeError, "production_bucket_access_not_denied"):
                self.canary.run(release_id="owned-release", production_database="postgres")
            storage.put_object.assert_not_called()


class HomeworkPreviewDriftTests(unittest.TestCase):
    """Run the real readonly drift function with Describe responses, never AWS."""

    CE = "academy-development-homework-video-ce"
    QUEUE = "academy-development-homework-video-queue"

    def setUp(self):
        self.fixture = {
            "params": {"homeworkPreviewDevelopment": {"computeEnvironmentName": self.CE, "queueName": self.QUEUE}},
            "ces": [{"computeEnvironmentName": name, "status": "VALID", "state": "ENABLED",
                     "computeResources": {"type": "EC2", "allocationStrategy": "BEST_FIT_PROGRESSIVE", "maxvCpus": 4, "instanceTypes": ["c6a.large"]}}
                    for name in ("academy-video-ce", "academy-ops-ce", self.CE)],
            "queues": [{"jobQueueName": name, "status": "VALID", "state": "ENABLED"}
                       for name in ("academy-video-queue", "academy-ops-queue", self.QUEUE)],
        }

    def drift(self):
        harness = r'''
$ErrorActionPreference = 'Stop'
$script:fixture = $env:DRIFT_FIXTURE | ConvertFrom-Json
$script:params = ($env:DRIFT_FIXTURE | ConvertFrom-Json -AsHashtable).params
$script:Region = 'qa-region'
$script:VideoCEName = 'academy-video-ce'; $script:OpsCEName = 'academy-ops-ce'
$script:SSOT_CE = @($script:VideoCEName, $script:OpsCEName)
$script:SSOT_Queue = @('academy-video-queue', 'academy-ops-queue')
$script:VideoCEMaxvCpus = 4; $script:OpsCEMaxvCpus = 4
$script:VideoCEInstanceTypes = @('c6a.large'); $script:OpsCEInstanceType = 'c6a.large'
$script:SSOT_ASG = @(); $script:SSOT_EventBridgeRule = @()
$script:ApiASGName = 'api'; $script:MessagingASGName = 'messaging'
$script:AiASGName = 'ai'; $script:ToolsASGName = 'tools'
$script:ApiLaunchTemplateName = 'qa-api-lt'
function Get-ParamsYaml { return $script:params }
function Invoke-AwsJson {
    param([object[]]$Arguments)
    switch ("$($Arguments[0]):$($Arguments[1])") {
        'batch:describe-compute-environments' { return [pscustomobject]@{computeEnvironments=$script:fixture.ces} }
        'batch:describe-job-queues' { return [pscustomobject]@{jobQueues=$script:fixture.queues} }
        'autoscaling:describe-auto-scaling-groups' { return [pscustomobject]@{AutoScalingGroups=@()} }
        'ec2:describe-launch-templates' { return [pscustomobject]@{LaunchTemplates=@()} }
        default { throw 'Unexpected AWS operation in readonly drift' }
    }
}
. (Join-Path $env:DRIFT_ROOT 'scripts/v1/core/diff.ps1')
$rows = @(Get-StructuralDrift)
'DRIFT_RESULT=' + (@{rows=$rows; productionCE=$script:SSOT_CE; productionQueue=$script:SSOT_Queue} | ConvertTo-Json -Depth 10 -Compress)
'''
        env = {key: value for key, value in os.environ.items() if not key.startswith(("AWS_", "ACADEMY_"))}
        env.update(DRIFT_ROOT=str(ROOT), DRIFT_FIXTURE=json.dumps(self.fixture))
        result = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-Command", harness],
                                capture_output=True, text=True, timeout=30, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = [line for line in result.stdout.splitlines() if line.startswith("DRIFT_RESULT=")]
        self.assertEqual(len(lines), 1, result.stdout + result.stderr)
        data = json.loads(lines[0].split("=", 1)[1])
        self.assertEqual(data["productionCE"], ["academy-video-ce", "academy-ops-ce"])
        self.assertEqual(data["productionQueue"], ["academy-video-queue", "academy-ops-queue"])
        return {row["Name"]: row["Action"] for row in data["rows"]}

    def test_only_declared_healthy_resources_are_noop(self):
        rows = self.drift()
        for name in (self.CE, self.QUEUE, "academy-video-ce", "academy-ops-queue"):
            self.assertEqual(rows[name], "NoOp")

    def test_development_unhealthy_missing_or_duplicate_requires_review(self):
        original = deepcopy(self.fixture)
        for group, field, name in (("ces", "computeEnvironmentName", self.CE), ("queues", "jobQueueName", self.QUEUE)):
            for fault in ("disabled", "invalid", "creating", "missing", "duplicate"):
                with self.subTest(resource=group, fault=fault):
                    self.fixture = deepcopy(original)
                    resource = next(row for row in self.fixture[group] if row[field] == name)
                    if fault == "missing":
                        self.fixture[group].remove(resource)
                    elif fault == "duplicate":
                        self.fixture[group].append(deepcopy(resource))
                    elif fault == "disabled":
                        resource["state"] = "DISABLED"
                    else:
                        resource["status"] = fault.upper()
                    self.assertEqual(self.drift()[name], "Review")

    def test_unknown_similarly_named_resources_are_not_exempt(self):
        for group, field, name in (("ces", "computeEnvironmentName", self.CE), ("queues", "jobQueueName", self.QUEUE)):
            extra = deepcopy(self.fixture[group][-1])
            extra[field] = name + "-unknown"
            self.fixture[group].append(extra)
        rows = self.drift()
        self.assertEqual(rows[self.CE + "-unknown"], "Review")
        self.assertEqual(rows[self.QUEUE + "-unknown"], "Review")

    def test_missing_incomplete_or_wrong_declaration_never_guesses_names(self):
        for declaration in (None, {}, {"computeEnvironmentName": self.CE},
                            {"computeEnvironmentName": self.CE, "queueName": self.QUEUE + "-other"}):
            with self.subTest(declaration=declaration):
                self.fixture["params"] = {} if declaration is None else {"homeworkPreviewDevelopment": declaration}
                rows = self.drift()
                self.assertEqual(rows[self.CE], "Review")
                self.assertEqual(rows[self.QUEUE], "Review")

    def test_production_repair_classification_is_unchanged(self):
        self.fixture["ces"][0]["status"] = "INVALID"
        self.fixture["queues"][0]["state"] = "DISABLED"
        rows = self.drift()
        self.assertEqual(rows["academy-video-ce"], "Recreate")
        self.assertEqual(rows["academy-video-queue"], "Update")


if __name__ == "__main__": unittest.main()
