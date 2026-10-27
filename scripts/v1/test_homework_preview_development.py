"""Isolation and failure boundaries for the dedicated preview runtime."""
import importlib.util
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


if __name__ == "__main__": unittest.main()
